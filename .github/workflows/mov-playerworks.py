"""Makes the PlayerWorks edits in FFmpeg's libavformat (mov.c, isom.h). Run in an FFmpeg checkout with the
files as git has them: python3 movpatch.py <ffmpeg dir>."""
import sys

root = sys.argv[1]


def edit(rel, pairs):
    p = root + '/' + rel
    s = open(p).read()
    for a, b in pairs:
        assert s.count(a) == 1, (rel, a[:70], s.count(a))
        s = s.replace(a, b)
    open(p, 'w').write(s)


edit('libavformat/isom.h', [
    ('''    int64_t moof_offset;
    int headers_read;
''', '''    int64_t moof_offset;
    int headers_read;
    int64_t pw_end; ///< PlayerWorks: the offset after this fragment's mdat when read lazily (0: unknown)
'''),
])

HELPERS = r'''/* PlayerWorks: a fragmented file without a fragment index (no sidx/mfra) is read lazily, a fragment at a
 * time (see mov_read_default). A seek to a time not read yet finds its fragment by the bytes instead of
 * reading every fragment up to it: an offset estimated from the time (between the nearest fragments known,
 * by the mvhd duration and the file size at first), the first moof after it read (its header only: tfdt
 * gives its time), and the range narrowed from there. A few reads, however far the seek. */
static int64_t pw_find_moof(AVIOContext *pb, int64_t from, int64_t limit)
{
    const int bufsize = 1 << 16;
    uint8_t *buf;
    int64_t pos = from, found = -1;

    if (from < 0 || limit <= from)
        return -1;
    limit = FFMIN(limit, from + (INT64_C(1) << 26)); /* a fragment of 64 MB at most */
    if (!(buf = av_malloc(bufsize)))
        return AVERROR(ENOMEM);
    while (pos < limit && found < 0) {
        int n, i;
        if (avio_seek(pb, pos, SEEK_SET) != pos)
            break;
        n = avio_read(pb, buf, bufsize);
        if (n < 16)
            break;
        for (i = 0; i + 16 <= n && pos + i < limit; i++) {
            if (AV_RL32(buf + i + 4) == MKTAG('m','o','o','f') && AV_RB32(buf + i + 8) == 16 &&
                AV_RL32(buf + i + 12) == MKTAG('m','f','h','d') && AV_RB32(buf + i) >= 24) {
                found = pos + i;
                break;
            }
        }
        pos += n - 15;
    }
    av_free(buf);
    return found;
}

static int64_t pw_frag_dts(MOVContext *mov, int index, int id)
{
    MOVFragmentStreamInfo *f = get_frag_stream_info(&mov->frag_index, index, id);
    return f ? f->tfdt_dts : AV_NOPTS_VALUE;
}

/* Whether the samples read so far cover timestamp (a fragment read starts at or before it and its last
 * sample is not before it). */
static int pw_time_read(MOVContext *mov, AVStream *st, int64_t timestamp)
{
    MOVStreamContext *sc = st->priv_data;
    FFStream *const sti = ffstream(st);
    int i, best = -1, first, end = sti->nb_index_entries;
    int64_t best_dts = 0;

    for (i = 0; i < mov->frag_index.nb_items; i++) {
        MOVFragmentStreamInfo *f = get_frag_stream_info(&mov->frag_index, i, sc->id);
        if (!f || f->tfdt_dts == AV_NOPTS_VALUE || f->index_entry < 0 || f->tfdt_dts > timestamp)
            continue;
        if (best < 0 || f->tfdt_dts > best_dts) {
            best = i;
            best_dts = f->tfdt_dts;
        }
    }
    if (best < 0)
        return 0;
    first = get_frag_stream_info(&mov->frag_index, best, sc->id)->index_entry;
    for (i = 0; i < mov->frag_index.nb_items; i++) {
        MOVFragmentStreamInfo *f = get_frag_stream_info(&mov->frag_index, i, sc->id);
        if (f && f->index_entry > first && f->index_entry < end)
            end = f->index_entry;
    }
    return end > first && end <= sti->nb_index_entries && timestamp <= sti->index_entries[end - 1].timestamp;
}

static int pw_seek_fragment_estimate(AVFormatContext *s, AVStream *st, int64_t timestamp)
{
    MOVContext *mov = s->priv_data;
    MOVStreamContext *sc = st->priv_data;
    AVIOContext *pb = s->pb;
    const double tb = av_q2d(st->time_base);
    /* (The length: the format's if known; for a fragmented file usually only mvhd's, in the movie timescale.) */
    const double total = s->duration > 0 ? s->duration / (double)AV_TIME_BASE :
                         mov->time_scale > 0 && mov->duration > 0 ? mov->duration / (double)mov->time_scale : 0;
    int64_t size, lo_off, hi_off, land_off = -1;
    double t, lo_t = 0, hi_t;
    int i, iter, bisect = 0;

    if (!mov->trex_count || mov->frag_index.nb_items < 1 || !(pb->seekable & AVIO_SEEKABLE_NORMAL) ||
        total <= 0 || (size = avio_size(pb)) <= 0 || pw_time_read(mov, st, timestamp))
        return 0;
    t = timestamp * tb;
    lo_off = mov->frag_index.item[0].moof_offset;
    hi_off = size;
    hi_t = total;
    for (i = 0; i < mov->frag_index.nb_items; i++) { /* the nearest fragments known on either side */
        int64_t d = pw_frag_dts(mov, i, sc->id), off = mov->frag_index.item[i].moof_offset;
        if (d == AV_NOPTS_VALUE)
            continue;
        if (d * tb <= t && off >= lo_off) {
            lo_off = off; lo_t = d * tb; land_off = off;
        } else if (d * tb > t && off < hi_off) {
            hi_off = off; hi_t = d * tb;
        }
    }
    for (iter = 0; iter < 16; iter++) {
        int64_t from, moof, d, start = lo_off + 1;
        int idx, ret;
        if (land_off >= 0) {
            idx = search_frag_moof_offset(&mov->frag_index, land_off);
            if (idx < mov->frag_index.nb_items && mov->frag_index.item[idx].pw_end > 0) {
                start = mov->frag_index.item[idx].pw_end;
                if (start >= hi_off)
                    break; /* the fragment after the one known before is the one known after: found */
            }
        }
        if (start >= hi_off)
            break;
        if (bisect)
            from = start + (hi_off - start) / 2;
        else
            from = lo_off + (int64_t)((hi_off - lo_off) * av_clipd((t - lo_t) / FFMAX(hi_t - lo_t, 0.001), 0, 1));
        from = av_clip64(from, start, hi_off - 1);
        moof = pw_find_moof(pb, from, hi_off);
        if (moof < 0) {
            if (from <= start)
                break;
            hi_off = from; /* no fragment starts between from and hi: it is before from */
            bisect = 1;
            continue;
        }
        bisect = 0;
        ret = mov_switch_root(s, moof, -1);
        if (ret == AVERROR_EOF)
            break;
        if (ret < 0)
            return ret;
        idx = search_frag_moof_offset(&mov->frag_index, moof);
        if (idx >= mov->frag_index.nb_items || mov->frag_index.item[idx].moof_offset != moof ||
            (d = pw_frag_dts(mov, idx, sc->id)) == AV_NOPTS_VALUE)
            break;
        if (d * tb <= t) {
            lo_off = moof; lo_t = d * tb; land_off = moof;
        } else {
            hi_off = moof; hi_t = d * tb;
        }
    }
    if (land_off >= 0) { /* read on after the fragment that has it */
        int idx = search_frag_moof_offset(&mov->frag_index, land_off);
        if (idx < mov->frag_index.nb_items && mov->frag_index.item[idx].pw_end > 0)
            mov->next_root_atom = mov->frag_index.item[idx].pw_end;
    }
    return 0;
}

static int mov_seek_fragment(AVFormatContext *s, AVStream *st, int64_t timestamp)'''

edit('libavformat/mov.c', [
    ('''    item->headers_read = 0;
    item->current = 0;
''', '''    item->headers_read = 0;
    item->pw_end = 0;
    item->current = 0;
'''),
    ('''                return err;
            }
            if (c->found_moov && c->found_mdat && a.size <= INT64_MAX - start_pos &&''',
     '''                return err;
            }
            /* PlayerWorks: a seekable file that is not fragmented (its moov has no mvex/trex) needs nothing
             * after its index and the first media data: stop here and take it as complete. No walk over every
             * top-level box to the end of the file now (a seek per mdat: minutes over a network), and none
             * later on a far seek either (as with -fflags +ignidx, which only postpones that walk). */
            if (c->found_moov && c->found_mdat && !c->trex_count &&
                (pb->seekable & AVIO_SEEKABLE_NORMAL)) {
                c->atom_depth --;
                return 0;
            }
            /* PlayerWorks: a fragmented one without a fragment index (no sidx/mfra) is read lazily, a fragment
             * at a time as it plays, instead of every moof to the end before the first picture; a far seek
             * finds its fragment by an estimate (pw_seek_fragment_estimate). */
            if (c->found_moov && c->found_mdat && c->trex_count && !c->frag_index.complete &&
                (pb->seekable & AVIO_SEEKABLE_NORMAL) && a.size <= INT64_MAX - start_pos) {
                if (c->frag_index.current >= 0 && c->frag_index.current < c->frag_index.nb_items) {
                    /* (Read: mov_switch_root must not read it again, its samples would go in twice.) */
                    c->frag_index.item[c->frag_index.current].headers_read = 1;
                    c->frag_index.item[c->frag_index.current].pw_end = start_pos + a.size;
                }
                c->next_root_atom = start_pos + a.size;
                c->atom_depth --;
                return 0;
            }
            if (c->found_moov && c->found_mdat && a.size <= INT64_MAX - start_pos &&'''),
    ('''        if (index + 1 < mov->frag_index.nb_items)
            mov->next_root_atom = mov->frag_index.item[index + 1].moof_offset;
        if (mov->frag_index.item[index].headers_read)
            return 0;''', '''        if (mov->frag_index.item[index].pw_end > 0) /* PlayerWorks: read lazily, the next one follows it */
            mov->next_root_atom = mov->frag_index.item[index].pw_end;
        else if (index + 1 < mov->frag_index.nb_items)
            mov->next_root_atom = mov->frag_index.item[index + 1].moof_offset;
        if (mov->frag_index.item[index].headers_read)
            return 0;'''),
    ('''static int mov_seek_fragment(AVFormatContext *s, AVStream *st, int64_t timestamp)''', HELPERS),
    ('''    if (!mov->frag_index.complete)
        return 0;

    index = search_frag_timestamp(''', '''    if (!mov->frag_index.complete)
        return pw_seek_fragment_estimate(s, st, timestamp);

    index = search_frag_timestamp('''),
])
print('edited')
