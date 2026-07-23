# train_student.py
  # InternVL3-78B(teacher) → InternVL3-1B(student) 지식 증류 파인튜닝
  # 문서 시나리오: 16프레임 × 448×448 × 타일1개(256토큰), LoRA, bf16, 비전인코더 freeze
  import argparse, json
  import torch
  from torch.utils.data import Dataset, DataLoader
  import torchvision.transforms as T
  from torchvision.transforms.functional import InterpolationMode
  from PIL import Image
  from transformers import AutoModel, AutoTokenizer
  from peft import LoraConfig, get_peft_model

  STUDENT_ID = "OpenGVLab/InternVL3-1B"
  INPUT_SIZE = 448
  NUM_FRAMES = 16

  # InternVL 이미지 토큰 (정석은 모델 config 에서 읽는 것)
  IMG_START, IMG_END, IMG_CTX = "<img>", "</img>", "<IMG_CONTEXT>"
  IMAGENET_MEAN = (0.485, 0.456, 0.406)
  IMAGENET_STD  = (0.229, 0.224, 0.225)

  # ── 공용 전처리 (teacher 데이터 생성과 동일 파이프라인) ────────────────────
  _transform = T.Compose([
      T.Lambda(lambda im: im.convert("RGB") if im.mode != "RGB" else im),
      T.Resize((INPUT_SIZE, INPUT_SIZE), interpolation=InterpolationMode.BICUBIC),
      T.ToTensor(),
      T.Normalize(IMAGENET_MEAN, IMAGENET_STD),
  ])

  def load_video_frames(frame_paths):
      """프레임당 타일 1개 (max_num=1): 각 프레임을 448×448 한 장으로.
      반환: [num_frames, 3, 448, 448]"""
      tiles = [_transform(Image.open(p)) for p in frame_paths]
      return torch.stack(tiles)

  def build_video_prompt(question, num_frames):
      """Frame1: <image>\n ... Frame16: <image>\n{question}"""
      frames = "".join(f"Frame{i+1}: <image>\n" for i in range(num_frames))
      return f"{frames}{question}"

  def expand_image_tokens(prompt, num_placeholders, tokens_per_tile):
      """각 <image> → <img>(<IMG_CONTEXT>×256)</img>. 프레임당 타일 1개라 프레임 수만큼."""
      ctx = IMG_CTX * tokens_per_tile
      for _ in range(num_placeholders):
          prompt = prompt.replace("<image>", f"{IMG_START}{ctx}{IMG_END}", 1)
      return prompt

  # ── 데이터셋 ──────────────────────────────────────────────────────────────
  class DistillDataset(Dataset):
      """teacher(78B) 출력을 정답으로 하는 student 학습셋 (data/train.jsonl)."""
      def __init__(self, jsonl, tokenizer, tokens_per_tile, max_len=6144):
          self.rows = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
          self.tok = tokenizer
          self.tokens_per_tile = tokens_per_tile
          self.max_len = max_len

      def __len__(self):
          return len(self.rows)

      def __getitem__(self, i):
          r = self.rows[i]
          pixel_values = load_video_frames(r["frames"])       # [16,3,448,448]
          num_frames = pixel_values.shape[0]

          # 프롬프트: <image> N개를 각각 256토큰으로 펼침
          prompt = build_video_prompt(r["question"], num_frames)
          prompt = expand_image_tokens(prompt, num_frames, self.tokens_per_tile)

          # user(프롬프트)는 마스킹(-100), answer 만 학습 대상
          prompt_ids = self.tok(prompt, add_special_tokens=False).input_ids
          answer_ids = self.tok(r["answer"] + self.tok.eos_token,
                                add_special_tokens=False).input_ids
          input_ids = (prompt_ids + answer_ids)[:self.max_len]
          labels    = ([-100] * len(prompt_ids) + answer_ids)[:self.max_len]

          return {
              "input_ids":    torch.tensor(input_ids),
              "labels":       torch.tensor(labels),
              "pixel_values": pixel_values,       # [num_frames,3,448,448]
              "num_frames":   num_frames,         # = 타일 수
          }

  def make_collate(pad_id):
      def collate(batch):
          maxlen = max(len(b["input_ids"]) for b in batch)
          def pad(x, val):
              return torch.cat([x, torch.full((maxlen - len(x),), val, dtype=x.dtype)])
          input_ids = torch.stack([pad(b["input_ids"], pad_id) for b in batch])
          return {
              "input_ids":      input_ids,
              "attention_mask": (input_ids != pad_id).long(),
              "labels":         torch.stack([pad(b["labels"], -100) for b in batch]),
              # 배치 내 모든 프레임 타일을 쌓고, image_flags 로 소속 표시 (전부 실제 이미지=1)
              "pixel_values":   torch.cat([b["pixel_values"] for b in batch], dim=0),
              "image_flags":    torch.ones(
                  sum(b["num_frames"] for b in batch), dtype=torch.long),
          }
      return collate

  # ── 학습 ──────────────────────────────────────────────────────────────────
  def main():
      ap = argparse.ArgumentParser()
      ap.add_argument("--data", default="data/train.jsonl")
      ap.add_argument("--out", default="out/student-ft")
      ap.add_argument("--epochs", type=int, default=1)
      ap.add_argument("--bs", type=int, default=1)            # 물리 배치 (문서 권장: 1~2)
      ap.add_argument("--accum", type=int, default=8)         # gradient accumulation
      ap.add_argument("--lr", type=float, default=1e-4)
      ap.add_argument("--grad-ckpt", action="store_true",     # 16GB 에서는 사실상 필수
                      help="gradient checkpointing (활성값 메모리 30~40%%↓)")
      args = ap.parse_args()

      tok = AutoTokenizer.from_pretrained(STUDENT_ID, trust_remote_code=True)
      model = AutoModel.from_pretrained(
          STUDENT_ID, torch_dtype=torch.bfloat16, trust_remote_code=True
      ).cuda()

      # 타일당 이미지 토큰 수(=256)는 모델이 안다. img_context 토큰 id 등록.
      tokens_per_tile = model.num_image_token
      model.img_context_token_id = tok.convert_tokens_to_ids(IMG_CTX)

      # ① 비전 인코더 freeze → 그래디언트/옵티마이저 상태 없음 (메모리 절감)
      for p in model.vision_model.parameters():
          p.requires_grad = False

      # ② 활성값 절감: gradient checkpointing
      if args.grad_ckpt:
          model.gradient_checkpointing_enable()
          model.config.use_cache = False

      # ③ LoRA: 언어모델 어텐션 투영에만 어댑터 (학습 대상 1% 미만)
      lora = LoraConfig(
          r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
          target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
          task_type="CAUSAL_LM",
          answer_ids = self.tok(r["answer"] + self.tok.eos_token,
                                add_special_tokens=False).input_ids
          input_ids = (prompt_ids + answer_ids)[:self.max_len]
          labels    = ([-100] * len(prompt_ids) + answer_ids)[:self.max_len]

          return {
              "input_ids":    torch.tensor(input_ids),
              "labels":       torch.tensor(labels),
              "pixel_values": pixel_values,       # [num_frames,3,448,448]
              "num_frames":   num_frames,         # = 타일 수
          }

  def make_collate(pad_id):
      def collate(batch):
          maxlen = max(len(b["input_ids"]) for b in batch)
          def pad(x, val):
              return torch.cat([x, torch.full((maxlen - len(x),), val, dtype=x.dtype)])
          input_ids = torch.stack([pad(b["input_ids"], pad_id) for b in batch])
          return {
              "input_ids":      input_ids,
              "attention_mask": (input_ids != pad_id).long(),
              "labels":         torch.stack([pad(b["labels"], -100) for b in batch]),
              # 배치 내 모든 프레임 타일을 쌓고, image_flags 로 소속 표시 (전부 실제 이미지=1)
              "pixel_values":   torch.cat([b["pixel_values"] for b in batch], dim=0),
              "image_flags":    torch.ones(
                  sum(b["num_frames"] for b in batch), dtype=torch.long),
          }
      return collate

  # ── 학습 ──────────────────────────────────────────────────────────────────
  def main():
      ap = argparse.ArgumentParser()
      ap.add_argument("--data", default="data/train.jsonl")
      ap.add_argument("--out", default="out/student-ft")
