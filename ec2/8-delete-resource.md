```
aws s3 rm s3://$BUCKET --recursive   # 안의 객체 전부 삭제
aws s3api delete-bucket --bucket $BUCKET --region $REGION  # 그다음 버킷 삭제
```
