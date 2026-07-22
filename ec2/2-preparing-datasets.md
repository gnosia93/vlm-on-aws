
### FineVideo 데이터셋의 의해 ###

* 영상 약 43,000개 / 3,400시간 구성되어있고 전체 용량이 수백 GB~TB 정도이다.
* WebDataset 포맷: tar 샤드 안에 .mp4(영상)와 .json(메타데이터)가 쌍으로 들어 있다.
* 각 샘플의 JSON에는 자체 택소노미 기반 카테고리(예: content_parent_category, content_fine_category)와 YouTube 메타데이터가 들어 있다.
* 게이트 데이터셋으로, HF 페이지에서 라이선스(CC) 동의를 먼저 해야 하고, 다운로드 시 HF_TOKEN이 필요하다.

> [!IMPORTANT]
> CC 라이선스 영상이라 재배포/저장 시 원본 라이선스와 저작자 표시(attribution) 조건을 지켜야 하는데, JSON의 provenance 필드를 함께 S3에 저장해두면 나중에 출처 추적이 된다.

### 1. EC2 생성하기 ###

데이터 준비 단계에서는 네트워크 대역폭과 디스크 성능이 좋은 CPU 인스턴스가 필요하다.
* 인스턴스: m7i.4xlarge 또는 c7i.4xlarge 정도 (네트워크 좋고 vCPU 넉넉). 대량이면 network-optimized(m7in)도 고려.
* 스토리지: 임시 스크래치용 로컬 NVMe 있는 타입이면 좋고, 없으면 EBS gp3 500GB~1TB.
* S3 버킷으로 다운로드 받은 파일을 업로드하므로 S3 쓰기 권한(vlm-s3-access) 이 필요하다.

```
aws ec2 run-instances \
  --iam-instance-profile Name=vlm-ec2-profile \
  --instance-type m7i.4xlarge \
  --image-id <ubuntu-22.04-ami> \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":1000,"VolumeType":"gp3"}}]' \
  ... (subnet, security-group 등)
```
인스턴스로 접속한 후 ffmpeg 및 hf 패키지를 설치한다. 
```
sudo apt-get update && sudo apt-get install -y python3-pip ffmpeg
pip install "datasets>=3.0" huggingface_hub hf_transfer boto3

export HF_TOKEN=hf_xxxxxxxxxxxx
export HF_HUB_ENABLE_HF_TRANSFER=1
```

### 2. 카테고리 필드 먼저 확인 ###
스크립트 짜기 전에 JSON 구조를 확인한다.
```
from datasets import load_dataset

ds = load_dataset("HuggingFaceFV/finevideo", split="train", streaming=True)
sample = next(iter(ds))
print(sample.keys())            # 보통 dict_keys(['mp4', 'json'])
import json
print(json.dumps(sample["json"], indent=2, ensure_ascii=False)[:3000])
```
여기서 카테고리가 어디에 들어있는지 확인하고(예: sample["json"]["content_metadata"]["content_parent_category"]),
아래 스크립트의 get_category()를 맞춰준다.

### 3. 다운로드 및 S3 적재 ###
스트리밍하면서 대상 카테고리만 골라 로컬에 임시 저장후 S3 로 업로드 한다.
```
import io
import os
import json
import boto3
from datasets import load_dataset

BUCKET = os.environ["BUCKET"]
PREFIX = "finevideo"                       # S3 최상위 경로
TARGET_CATEGORIES = {"Sports", "Cooking"}  # 원하는 카테고리로 교체
MAX_PER_CATEGORY = 500                     # 카테고리당 상한 (None이면 무제한)

s3 = boto3.client("s3")

def get_category(meta: dict) -> str | None:
    # 2단계에서 확인한 실제 경로로 맞추세요
    cm = meta.get("content_metadata", {})
    return cm.get("content_parent_category") or meta.get("categories")

def s3_put_bytes(key: str, data: bytes):
    s3.upload_fileobj(io.BytesIO(data), BUCKET, key)

def main():
    ds = load_dataset("HuggingFaceFV/finevideo", split="train", streaming=True)
    counts = {c: 0 for c in TARGET_CATEGORIES}

    for i, sample in enumerate(ds):
        meta = sample["json"]
        cat = get_category(meta)
        if cat not in TARGET_CATEGORIES:
            continue
        if MAX_PER_CATEGORY and counts[cat] >= MAX_PER_CATEGORY:
            if all(counts[c] >= MAX_PER_CATEGORY for c in TARGET_CATEGORIES):
                break
            continue

        # 안정적인 식별자 (youtube id 있으면 그걸 사용)
        vid = meta.get("youtube_id") or meta.get("id") or f"idx_{i:06d}"
        safe_cat = cat.replace(" ", "_").lower()

        # mp4 bytes 추출 (datasets 버전에 따라 형태가 다를 수 있음)
        mp4 = sample["mp4"]
        video_bytes = mp4 if isinstance(mp4, (bytes, bytearray)) else open(mp4, "rb").read()

        base = f"{PREFIX}/{safe_cat}/{vid}"
        s3_put_bytes(f"{base}/video.mp4", video_bytes)
        s3_put_bytes(f"{base}/metadata.json",
                     json.dumps(meta, ensure_ascii=False).encode("utf-8"))

        counts[cat] += 1
        print(f"[{sum(counts.values())}] {cat} -> {base}")

    # 매니페스트(색인) 저장
    manifest = {"categories": counts, "prefix": PREFIX, "target": list(TARGET_CATEGORIES)}
    s3_put_bytes(f"{PREFIX}/manifest.json",
                 json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
    print("done:", counts)

if __name__ == "__main__":
    main()
```

아래 명령어로 실행한다. 
```
export BUCKET=your-bucket-name
python3 prepare_finevideo.py
```
