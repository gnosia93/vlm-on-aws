aws s3 sync는 기본 설정이 보수적이라, 78B처럼 큰 모델은 병렬도를 올리면 훨씬 빨라진다.

### 1) 동시 요청 수 늘리기 (효과 가장 큼)

기본 병렬 다운로드가 10개인데, 인스턴스 성능이 좋으면 확 올릴 수 있다.
```
aws configure set default.s3.max_concurrent_requests 40
aws configure set default.s3.multipart_chunksize 64MB
aws configure set default.s3.multipart_threshold 64MB
```

그다음 그냥 원래 명령 실행:
```
aws s3 sync s3://${BUCKET}/models/internvl3-78b/ \
  /opt/dlami/nvme/hf-cache/hub/models--OpenGVLab--InternVL3-78B/
```
- max_concurrent_requests 40 — 동시에 40개 전송 (코어·대역폭 넉넉하면 더 올려도 됨)
- multipart_chunksize/threshold — 큰 safetensors 파일을 여러 조각으로 병렬 전송

### 2) 리전이 같은지 확인 ###

버킷과 EC2 인스턴스가 같은 리전이어야 최고 속도가 나온다. 다른 리전이면 인터넷 구간을 타서 느리다.
```
aws s3api get-bucket-location --bucket ${BUCKET}
```

### 3) VPC S3 Gateway Endpoint (같은 리전일 때 무료+빠름) ###

인스턴스가 NAT 게이트웨이를 통해 S3에 접근 중이면 병목이 될 수 있어요. S3 Gateway Endpoint를 VPC에 붙이면 트래픽이 AWS 내부망으로 직행해서 빠르고 데이터
전송료도 안 나옵니다. (인프라 설정이라 한 번 해두면 계속 유효)

### 4) 대상 디스크 확인 — 이미 좋음 ###

/opt/dlami/nvme/... 경로를 보니 로컬 NVMe SSD에 받고 있네요. 이건 이미 최선이에요. EBS보다 훨씬 빠르고, 대용량 모델 캐시 위치로 이상적입니다.

### 5) 더 빠른 도구: s5cmd (극단적으로 크면) ###

aws cli보다 몇 배 빠른 전용 도구예요. 수백 GB급이면 고려할 만 하다.
```
s5cmd sync "s3://${BUCKET}/models/internvl3-78b/*" \
  /opt/dlami/nvme/hf-cache/hub/models--OpenGVLab--InternVL3-78B/
```
