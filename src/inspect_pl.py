import polars as pl

path = "/home/ubuntu/.cache/huggingface/hub/datasets--HuggingFaceFV--finevideo/snapshots/84c74091e1c6ee7a5dffabfafb5c9033e4718883/data/train-00000-of-01357.parquet"

j = pl.col("json").struct

# 미리보기 (직속 필드 + content_metadata 안의 title)
df = (
    pl.scan_parquet(path)
      .select(
          j.field("content_parent_category").alias("parent_cat"),
          j.field("content_fine_category").alias("fine_cat"),
          j.field("content_metadata").struct.field("title").alias("title"),
          j.field("duration_seconds").alias("duration"),
          j.field("resolution").alias("resolution"),
          j.field("youtube_title").alias("yt_title"),
      )
      .head(5)
      .collect()
)
print(df)

# 이 샤드의 상위 카테고리 분포
dist = (
    pl.scan_parquet(path)
      .select(j.field("content_parent_category").alias("cat"))
      .collect()
      .group_by("cat")
      .len()
      .sort("len", descending=True)
)
print(dist)


