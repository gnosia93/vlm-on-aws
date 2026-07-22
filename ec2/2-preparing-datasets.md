## 데이터셋 준비하기 ##
### FineVideo 데이터셋의 이해 ###

* 영상 약 43,000개 / 3,400시간 구성되어있고 전체 용량이 수백 GB~TB 정도이다.
* parquet 포맷: mp4(영상)와 json(메타데이터)가 쌍으로 들어 있다.
* 각 샘플의 JSON에는 자체 택소노미 기반 카테고리(예: content_parent_category, content_fine_category)와 YouTube 메타데이터가 들어 있다.
* 게이트 데이터셋으로, HF 페이지에서 라이선스(CC) 동의를 먼저 해야 하고, 다운로드 시 HF_TOKEN이 필요하다.

> [!IMPORTANT]
> CC 라이선스 영상이라 재배포/저장 시 원본 라이선스와 저작자 표시(attribution) 조건을 지켜야 하는데, JSON의 provenance 필드를 함께 S3에 저장해두면 나중에 출처 추적이 된다.

### 1. hf 토큰 발급 ###
* https://huggingface.co/ 이동하여 회원 가입 후, 
* https://huggingface.co/settings/tokens 로 이동하여 우측 상단의 + Create new token 버튼을 클릭한 후, 
* Read 타입의 토큰을 발급 받는다. 
![](https://github.com/gnosia93/vlm-on-eks/blob/main/images/hf-token.png)

### 2. EC2 생성하기 ###

```
export REGION=ap-northeast-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text)
export SG_ID=$(aws ec2 describe-security-groups --region $REGION \
  --filters "Name=group-name,Values=vlm-sg" \
  --query "SecurityGroups[].GroupId" \
  --output text)
export SUBNET_ID=$(aws ec2 describe-subnets --region $REGION \
  --filters "Name=tag:Name,Values=vlm-public-subnet" \
  --query "Subnets[0].SubnetId" \
  --output text)
export BUCKET=vlm-data-${ACCOUNT_ID}-${REGION}

echo "\n-------------------------------------"
echo "REGION: $REGION"
echo "ACCOUNT_ID: $ACCOUNT_ID"
echo "SG_ID: $SG_ID"
echo "SUBNET_ID: $SUBNET_ID"
echo "BUCKET: $BUCKET"
```

데이터 준비 단계에서는 네트워크 대역폭과 디스크 성능이 좋은 CPU 인스턴스가 필요하다.
* 인스턴스: m7g.4xlarge
* 스토리지: 임시 스크래치용 로컬 NVMe 있는 타입이면 좋고, 없으면 EBS gp3 500GB~1TB.
* S3 버킷으로 다운로드 받은 파일을 업로드하므로 S3 쓰기 권한(vlm-s3-access) 이 필요하다.

```
AMI_ID=$(aws ssm get-parameter \
  --region $REGION \
  --name /aws/service/canonical/ubuntu/server/22.04/stable/current/arm64/hvm/ebs-gp2/ami-id \
  --query 'Parameter.Value' --output text)
echo $AMI_ID

aws ec2 run-instances \
  --region $REGION \
  --image-id $AMI_ID \
  --instance-type m7g.4xlarge \
  --security-group-ids $SG_ID \
  --subnet-id $SUBNET_ID \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":600,"VolumeType":"gp3","Throughput":500,"Iops":6000,"DeleteOnTermination":true}}]' \
  --iam-instance-profile Name=vlm-ec2-profile \
  --instance-initiated-shutdown-behavior terminate \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=data-preprocessing}]' \
  --count 1
```

### 3. 인스턴스 접속하기 ####
system manager 를 이용하여 인스턴스에 접속 한다. 클라이이언트가 맥 os 인 경우 플러그인을 설치가 필요하다. 
```
brew install --cask session-manager-plugin
```

접속할 인스턴스를 조회하고, system manager 를 이용하여 로그인한다.  
```
INSTANCE=$(aws ssm describe-instance-information --region $REGION \
  --filters "Key=tag:Name,Values=data-preprocessing" \
  --query "InstanceInformationList[].InstanceId" \
  --output text)
echo "INSTANCE: $INSTANCE"

aws ssm start-session --target $INSTANCE --region $REGION

sudo su ubuntu
```


인스턴스로 접속한 후 ffmpeg 및 hf 패키지를 설치한다. 
```
cd
sudo apt-get update && sudo apt-get install -y python3-pip ffmpeg
pip install "datasets>=3.0" huggingface_hub hf_transfer boto3
```

### 4. 카테고리 필드 먼저 확인 ###
위에서 발급받은 hf 토큰을 아래와 같이 설정하고, 
```
export HF_TOKEN=hf_xxxxxxxxxxxx
export HF_XET_HIGH_PERFORMANCE=1
```

https://huggingface.co/datasets/HuggingFaceFV/finevideo 이동하여 Gate Model 에 대한 License 에 동의 한 후, 
아래 파이썬 스크립트를 이용하여 JSON 구조를 확인한다. 
```
pip install -U --user polars

git clone https://github.com/gnosia93/vlm-on-eks.git
cd vlm-on-eks/src

python3 inspect_pl.py
```
[결과]
![](https://github.com/gnosia93/vlm-on-eks/blob/main/images/inspect_pl.png)


### 5. 다운로드 및 S3 적재 ###
스트리밍하면서 대상 카테고리만 골라 로컬에 임시 저장후 S3 로 업로드 한다.
```
TOKEN=$(curl -sX PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
MAC=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/mac)
ACCOUNT_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/network/interfaces/macs/${MAC}/owner-id)

export ACCOUNT_ID REGION
export BUCKET=vlm-data-${ACCOUNT_ID}-${REGION}
echo "BUCKET: $BUCKET"

# 오래 걸리니 tmux 안에서 (SSM 세션 끊겨도 계속 돌게)
tmux new -s ingest
python3 prepare_finevideo.py
# Ctrl+b, d 로 detach → 나중에 tmux attach -t ingest 로 재확인
```

### 6. S3 데이터 레이아웃 ###
파이프라인 후속 단계(추론/파인튜닝)가 쉽게 참조하도록 카테고리별로 나눠 준다.
```
s3://<BUCKET>/finevideo/
├── manifest.json                 # 전체 색인 (카테고리별 개수 등)
├── sports/
│   ├── <video_id>/
│   │   ├── video.mp4
│   │   └── metadata.json
│   └── ...
└── cooking/
    └── <video_id>/
        ├── video.mp4
        └── metadata.json
```

* 재개(resume) 로직·병렬 처리까지 넣은 버전으로 확장 필요.


## 모델 가중치 S3 저장하기 ##

허깅페이스 cli 로 OpenGVLab/InternVL3-78B 모델의 가중치를 다운로드 받고, S3 로 업로드 한다. 
```
huggingface-cli download OpenGVLab/InternVL3-78B \
  --local-dir /mnt/data/internvl3-78b

aws s3 sync /mnt/data/internvl3-78b/ s3://${BUCKET}/models/internvl3-78b/
```
