#!/bin/bash
# See playerworks-test.yml. "build": two minimal FFmpeg builds; "compare": the tests.
set -e
BASE=094a2f8a2a
CFG="--disable-everything --disable-doc --disable-network --disable-autodetect --enable-ffmpeg
     --enable-protocol=file --enable-demuxer=mov --enable-muxer=mp4,mov,framemd5,null
     --enable-encoder=mpeg4,pcm_s16le --enable-decoder=mpeg4 --enable-indev=lavfi
     --enable-filter=testsrc2,sine,format,scale,aresample,anull,null --enable-swscale --enable-swresample"
case "$1" in
build)
  git worktree add -q ../base "$BASE"
  for t in base changed; do
    src=$([ $t = base ] && echo ../base || echo .)
    mkdir -p ../build-$t && (cd ../build-$t && "$OLDPWD/$src/configure" $CFG >/dev/null && make -s -j"$(nproc)" ffmpeg) \
      || { echo "build $t failed"; exit 1; }
    echo "built $t: $(../build-$t/ffmpeg -version | head -1)"
  done
  # (each from its own tree)
  ;;
compare)
  B=../build-base/ffmpeg; C=../build-changed/ffmpeg; W=../work; mkdir -p $W
  # Test files: 6 minutes of video (keyframe every 2 s) and sound.
  SRC="-f lavfi -i testsrc2=size=640x360:rate=25:duration=360 -f lavfi -i sine=frequency=440:duration=360"
  ENC="-c:v mpeg4 -q:v 6 -g 50 -c:a pcm_s16le -shortest"
  $C -v error -y $SRC $ENC $W/plain.mp4                                                         # moov at the end
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
    for ss in 30 150 300 355; do
      $B -v error -ss $ss -i $W/$f.mp4 -t 2 -map 0 -c copy -f framemd5 $W/$f.ss$ss.base
      $C -v error -ss $ss -i $W/$f.mp4 -t 2 -map 0 -c copy -f framemd5 $W/$f.ss$ss.changed
      r="identical"; cmp -s $W/$f.ss$ss.base $W/$f.ss$ss.changed || { r="DIFFERENT"; fail=1; }
      first=$(grep -v '^#' $W/$f.ss$ss.changed | head -1 | cut -d, -f1-3 | tr -d ' ')
      echo "  seek $ss s: $r (first packet stream,dts,pts: $first); reads base $(stats $B -v debug -ss $ss -i $W/$f.mp4 -t 0.2 -map 0 -c copy -f null -), changed $(stats $C -v debug -ss $ss -i $W/$f.mp4 -t 0.2 -map 0 -c copy -f null -)"
      [ $r = DIFFERENT ] && diff $W/$f.ss$ss.base $W/$f.ss$ss.changed | head -6
    done
  done
  exit $fail
  ;;
esac
