/* Test only: seeks the way mpv's demux_lavf does (av_seek_frame on stream -1, AVSEEK_FLAG_BACKWARD) after
 * playing PLAY seconds from the start, then prints the video packets' times for 20 s.
 * mpvlike <file> <play seconds> <target seconds> */
#include <stdio.h>
#include <stdlib.h>
#include <libavformat/avformat.h>

int main(int argc, char **argv)
{
    AVFormatContext *fc = NULL;
    AVPacket *pkt = av_packet_alloc();
    double play = atof(argv[2]), target = atof(argv[3]);
    int v, ret;

    if (avformat_open_input(&fc, argv[1], NULL, NULL) < 0 || avformat_find_stream_info(fc, NULL) < 0)
        return 1;
    v = av_find_best_stream(fc, AVMEDIA_TYPE_VIDEO, -1, -1, NULL, 0);
    while (av_read_frame(fc, pkt) >= 0) {   /* play from the start first */
        int s = pkt->stream_index;
        double t = pkt->dts * av_q2d(fc->streams[s]->time_base);
        av_packet_unref(pkt);
        if (s == v && t >= play)
            break;
    }
    ret = av_seek_frame(fc, -1, (int64_t)(target * AV_TIME_BASE), AVSEEK_FLAG_BACKWARD);
    if (ret < 0)
        printf("seek failed %d\n", ret);
    while (av_read_frame(fc, pkt) >= 0) {
        int s = pkt->stream_index;
        double t = pkt->dts * av_q2d(fc->streams[s]->time_base);
        if (s == v)
            printf("%.3f\n", t);
        av_packet_unref(pkt);
        if (s == v && t > target + 20)
            break;
    }
    avformat_close_input(&fc);
    av_packet_free(&pkt);
    return 0;
}
