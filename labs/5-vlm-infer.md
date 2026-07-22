* Dockerfile (s5cmd 추가)
```
FROM vllm/vllm-openai:v0.6.6.post1

WORKDIR /app

# s5cmd (S3 고속 병렬 전송) 설치
RUN curl -sL https://github.com/peak/s5cmd/releases/download/v2.2.2/s5cmd_2.2.2_Linux-64bit.tar.gz \
      | tar -xz -C /usr/local/bin s5cmd \
    && pip install --no-cache-dir pillow==11.0.0 requests==2.32.3 boto3==1.35.76

COPY src/ /app/src/
ENV PYTHONPATH=/app/src

ENTRYPOINT []
CMD ["python", "/app/src/run_worker.py"]
```


* vlm-batch-config.yaml
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

* vlm-batch-infer.yaml
```
# InternVL3-78B 배치 인퍼런스: g7e 4-GPU 노드 2대에서 TP=4 파드 2개 동시 실행.
apiVersion: batch/v1
kind: Job
metadata:
  name: vlm-batch-infer
  namespace: vlm-batch
spec:
  completions: 2            # NUM_SHARDS와 일치 (모델 재로딩 방지 위해 파드=샤드)
  parallelism: 2            # 2대 동시 실행
  completionMode: Indexed
  backoffLimit: 8           # 실패 시 재시도 (resume으로 이어서 처리)
  template:
    metadata:
      labels:
        app: vlm-batch-infer
    spec:
      restartPolicy: Never
      serviceAccountName: vlm-batch-sa      # IRSA로 S3 권한
      # g7e 4-GPU 노드에만 스케줄. 파드당 노드 하나를 통째로 씀.
      nodeSelector:
        node.kubernetes.io/instance-type: g7e.24xlarge
      tolerations:
        - key: nvidia.com/gpu
          operator: Exists
          effect: NoSchedule
      # 두 파드를 서로 다른 노드에 분산 (노드당 파드 1개)
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchLabels:
                  app: vlm-batch-infer
              topologyKey: kubernetes.io/hostname

      # 시작 시 S3 가중치를 로컬 NVMe로 병렬 다운로드 (EFS 대신)
      initContainers:
        - name: fetch-weights
          image: YOUR_REGISTRY/vllm-batch-inference:latest   # <-- 교체
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -e
              echo "syncing weights from ${MODEL_S3_URI} ..."
              s5cmd sync "${MODEL_S3_URI}*" /models/InternVL3-78B/
              echo "done."
          envFrom:
            - configMapRef:
                name: vlm-batch-config
          volumeMounts:
            - name: model-local
              mountPath: /models

      containers:
        - name: worker
          image: YOUR_REGISTRY/vllm-batch-inference:latest   # <-- 교체
          imagePullPolicy: IfNotPresent
          command: ["python", "/app/src/run_worker.py"]
          envFrom:
            - configMapRef:
                name: vlm-batch-config
          env:
            - name: NUM_SHARDS
              value: "2"            # completions와 동일
          resources:
            limits:
              nvidia.com/gpu: 4     # TENSOR_PARALLEL_SIZE와 반드시 일치
            requests:
              cpu: "24"
              memory: 200Gi
          volumeMounts:
            - name: model-local
              mountPath: /models      # 로컬 NVMe의 가중치
            - name: scratch
              mountPath: /scratch      # 로컬 임시 결과
            - name: dshm
              mountPath: /dev/shm
      volumes:
        - name: model-local
          emptyDir: {}          # 노드 로컬 NVMe (ephemeral storage가 NVMe여야 함)
        - name: scratch
          emptyDir: {}
        - name: dshm
          emptyDir:
            medium: Memory
            sizeLimit: 24Gi     # TP=4 프로세스 간 통신용
```


