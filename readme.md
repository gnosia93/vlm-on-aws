
* mock 데이터 생성
```
brew install ffmpeg-full

ffmpeg -f lavfi -i "testsrc=duration=10:size=640x480:rate=30" \
       -vf "drawtext=text='clip_00042 frame %{n}':fontsize=24:fontcolor=white:x=20:y=20" \
       -pix_fmt yuv420p clip_00042.mp4
```
