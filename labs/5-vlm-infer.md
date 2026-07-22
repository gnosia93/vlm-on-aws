
[configmap.yaml]
```
apiVersion: v1
kind: ConfigMap
metadata:
  name: vlm-batch-config
  namespace: vlm-batch
data:
  # 로컬 NVMe로 동기화된 가중치 경로에서 로드
  MODEL: "/models/InternVL3-78B"
  MODEL_S3_URI: "s3://my-vlm-data-bucket/models/InternVL3-78B/"
  TENSOR_PARALLEL_SIZE: "4"        # g7e 4-GPU → TP=4
  MAX_MODEL_LEN: "16384"           # 96GB/GPU라 컨텍스트 여유 있음
  GPU_MEMORY_UTILIZATION: "0.92"
  MAX_IMAGES_PER_PROMPT: "1"
  MAX_DYNAMIC_PATCH: "12"
  DTYPE: "bfloat16"
  TEMPERATURE: "0.2"
  TOP_P: "0.9"
  MAX_TOKENS: "1024"
  SEED: "0"

  S3_BUCKET: "my-vlm-data-bucket"
  INPUT_MANIFEST_KEY: "input/manifest.jsonl"
  IMAGE_PREFIX: "input/images/"
  OUTPUT_PREFIX: "output/run-2026-07-22/"
  AWS_REGION: "ap-northeast-2"

  SCRATCH_DIR: "/scratch"
  WRITE_BATCH_SIZE: "48"           # 메모리 여유가 커서 배치 키움
  UPLOAD_EVERY: "128"
  SYSTEM_PROMPT: "당신은 이미지를 정확하고 사실에 근거해 설명하는 어시스턴트입니다."
  DEFAULT_PROMPT: "이미지를 한국어로 자세히 설명해줘."

```
