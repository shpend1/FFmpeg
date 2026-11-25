#!/bin/bash
# See playerworks-test.yml. "build": two minimal FFmpeg builds; "compare": the tests.
set -e
BASE=094a2f8a2a
CFG="--disable-everything --disable-doc --disable-network --disable-autodetect --enable-ffmpeg
     --enable-protocol=file --enable-demuxer=mov --enable-muxer=mp4,mov,framemd5,null
     --enable-encoder=mpeg4,aac --enable-decoder=mpeg4,aac,wrapped_avframe,pcm_s16le --enable-indev=lavfi
     --enable-filter=testsrc2,sine,format,aformat,scale,aresample,anull,null --enable-swscale --enable-swresample"
case "$1" in
build)
  git worktree add -q ../base "$BASE"
  git worktree add -q ../orig "$BASE" && python3 .github/workflows/mov-playerworks.py ../orig
  for t in base orig changed; do
    src=$([ $t = changed ] && echo . || echo ../$t)
    mkdir -p ../build-$t && (cd ../build-$t && "$OLDPWD/$src/configure" $CFG >/dev/null && make -s -j"$(nproc)" ffmpeg) \
      || { echo "build $t failed"; exit 1; }
    echo "built $t: $(../build-$t/ffmpeg -version | head -1)"
  done
  # (each from its own tree)
  ;;
compare)
  B=../build-base/ffmpeg; C=../build-changed/ffmpeg; O=../build-orig/ffmpeg; W=../work; mkdir -p $W
  # Test files: 6 minutes of video (keyframe every 2 s) and sound.
  SRC="-f lavfi -i testsrc2=size=640x360:rate=25:duration=360 -f lavfi -i sine=frequency=440:duration=360"
  ENC="-c:v mpeg4 -q:v 6 -g 50 -c:a aac -b:a 64k -shortest"
  $C -hide_banner -muxers 2>/dev/null | grep -E ' (mp4|mov|framemd5|null) ' || true
  $C -hide_banner -encoders 2>/dev/null | grep -E ' (mpeg4|aac) ' || true
  $C -v verbose -y $SRC $ENC $W/plain.mp4 2>&1 | tail -25                                       # moov at the end
  [ -s $W/plain.mp4 ] || { echo "could not make the test file"; exit 1; }
  $C -v error -y $SRC $ENC -movflags +frag_keyframe+empty_moov+skip_trailer $W/frag-noindex.mp4  # no sidx, no mfra
  $C -v error -y $SRC $ENC -movflags +frag_keyframe+empty_moov $W/frag-mfra.mp4                 # with mfra
  ls -l $W
  fail=0
  stats() {  # bytes read and seeks of the input, from the AVIOContext statistics (-v debug)
    "$@" 2>&1 | grep -m1 'Statistics:.*bytes read' | sed -E 's/.*Statistics: ([0-9]+) bytes read, ([0-9]+) seeks.*/\1 bytes, \2 seeks/'
  }
  for f in plain frag-noindex frag-mfra; do
    echo "== $f.mp4"
    $B -v error -i $W/$f.mp4 -map 0 -c copy -f framemd5 $W/$f.full.base
    $C -v error -i $W/$f.mp4 -map 0 -c copy -f framemd5 $W/$f.full.changed
    if cmp -s $W/$f.full.base $W/$f.full.changed; then echo "  full read: identical ($(grep -vc '^#' $W/$f.full.base) packets)";
    else echo "  full read: DIFFERENT"; diff $W/$f.full.base $W/$f.full.changed | head -8; fail=1; fi
    echo "  open + first second, base:    $(stats $B -v debug -i $W/$f.mp4 -t 1 -map 0 -c copy -f null -)"
    echo "  open + first second, changed: $(stats $C -v debug -i $W/$f.mp4 -t 1 -map 0 -c copy -f null -)"
    # Seeks, timestamps kept as in the file (-copyts): where each lands (the first video packet, its time in
    # seconds: video is 1/12800) and the first 5 video packets.
    for ss in 30 150 300 355; do
      for t in base orig changed; do
        bin=$([ $t = base ] && echo $B || { [ $t = orig ] && echo $O || echo $C; })
        $bin -v warning -ss $ss -copyts -i $W/$f.mp4 -frames:v 450 -map 0:v -c copy -f framemd5 $W/$f.ss$ss.$t 2> $W/$f.ss$ss.$t.err
      done
      land() { grep -v '^#' $1 | head -1 | awk -F, '{ printf "%.2f s", $2 / 12800 }'; }
      cnt() { grep -vc '^#' $1; }
      r="identical"; cmp -s $W/$f.ss$ss.base $W/$f.ss$ss.changed || { r="DIFFERENT"; fail=1; }
      echo "  seek $ss s: $r; base lands $(land $W/$f.ss$ss.base) ($(cnt $W/$f.ss$ss.base) packets), original PlayerWorks edit $(land $W/$f.ss$ss.orig), changed $(land $W/$f.ss$ss.changed) ($(cnt $W/$f.ss$ss.changed)); reads base $(stats $B -v debug -ss $ss -i $W/$f.mp4 -t 0.2 -map 0 -c copy -f null -), changed $(stats $C -v debug -ss $ss -i $W/$f.mp4 -t 0.2 -map 0 -c copy -f null -)"
      if [ $r = DIFFERENT ] && [ $f = frag-noindex ]; then
        echo "    packets: base $(cnt $W/$f.ss$ss.base), changed $(cnt $W/$f.ss$ss.changed); first differences (stream, dts, pts, duration, size):"
        diff <(grep -v '^#' $W/$f.ss$ss.base | cut -d, -f1-5) <(grep -v '^#' $W/$f.ss$ss.changed | cut -d, -f1-5) | head -12 | sed 's/^/      /'
      fi
      # (A gap: where the changed build's video jumps by more than a frame.)
      awk -F, '!/^#/ { t = $2 / 12800; if (p != "" && t - p > 0.1) printf "    changed skips %.2f -> %.2f s
", p, t; p = t }' $W/$f.ss$ss.changed | head -3
      [ $f = frag-noindex ] && [ $ss != 30 ] && $C -v trace -ss $ss -i $W/$f.mp4 -frames:v 1 -map 0:v -c copy -f null - 2>&1 | grep -E 'seek estimate' | head -60
      for t in base changed; do [ -s $W/$f.ss$ss.$t.err ] && sed "s/^/    $t: /" $W/$f.ss$ss.$t.err | head -3; done
    done
  done
  exit $fail
  ;;
esac
