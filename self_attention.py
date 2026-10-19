"""PyTorchで使えるSelf-Attentionベースのテキストモデル。

既存のClaude Code経路とは独立しており、学習用の分類モデルや
JARVIS固有の分類タスクの土台として利用できます。
"""
from __future__ import annotations

import torch
from torch import Tensor, nn


class SelfAttentionEncoder(nn.Module):
    """トークン列をSelf-Attentionで文脈化するEncoder。"""

    def __init__(
        self,
        vocab_size: int,
        embedding_dim: int = 128,
        num_heads: int = 4,
        num_layers: int = 2,
        feedforward_dim: int | None = None,
        max_length: int = 512,
        dropout: float = 0.1,
        padding_idx: int = 0,
    ) -> None:
        super().__init__()
        if vocab_size <= 0:
            raise ValueError("vocab_size must be positive")
        if embedding_dim <= 0 or max_length <= 0:
            raise ValueError("embedding_dim and max_length must be positive")
        if embedding_dim % num_heads:
            raise ValueError("embedding_dim must be divisible by num_heads")
        if num_layers <= 0:
            raise ValueError("num_layers must be positive")

        hidden_dim = feedforward_dim or embedding_dim * 4
        self.max_length = max_length
        self.token_embedding = nn.Embedding(vocab_size, embedding_dim, padding_idx=padding_idx)
        self.position_embedding = nn.Embedding(max_length, embedding_dim)
        layer = nn.TransformerEncoderLayer(
            d_model=embedding_dim,
            nhead=num_heads,
            dim_feedforward=hidden_dim,
            dropout=dropout,
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        self.transformer = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.output_norm = nn.LayerNorm(embedding_dim)

    def forward(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> Tensor:
        """Returns contextualized embeddings with shape ``(batch, sequence, hidden)``."""
        if input_ids.ndim != 2:
            raise ValueError("input_ids must have shape (batch, sequence)")
        _, sequence_length = input_ids.shape
        if sequence_length == 0 or sequence_length > self.max_length:
            raise ValueError(f"sequence length must be between 1 and {self.max_length}")

        if attention_mask is None:
            attention_mask = input_ids.ne(0)
        if attention_mask.shape != input_ids.shape:
            raise ValueError("attention_mask must have the same shape as input_ids")
        attention_mask = attention_mask.to(dtype=torch.bool, device=input_ids.device)

        positions = torch.arange(sequence_length, device=input_ids.device).unsqueeze(0)
        embeddings = self.token_embedding(input_ids) + self.position_embedding(positions)
        encoded = self.transformer(embeddings, src_key_padding_mask=~attention_mask)
        return self.output_norm(encoded)

    @staticmethod
    def mean_pool(encoded: Tensor, attention_mask: Tensor) -> Tensor:
        """Paddingを除外して系列を1ベクトルに集約する。"""
        if encoded.ndim != 3 or attention_mask.shape != encoded.shape[:2]:
            raise ValueError("encoded and attention_mask shapes are incompatible")
        weights = attention_mask.to(dtype=encoded.dtype).unsqueeze(-1)
        return (encoded * weights).sum(dim=1) / weights.sum(dim=1).clamp_min(1)


class SelfAttentionClassifier(nn.Module):
    """Self-Attention Encoderに分類ヘッドを付けたモデル。"""

    def __init__(self, vocab_size: int, num_classes: int, **encoder_kwargs: object) -> None:
        super().__init__()
        if num_classes <= 0:
            raise ValueError("num_classes must be positive")
        self.encoder = SelfAttentionEncoder(vocab_size, **encoder_kwargs)
        embedding_dim = self.encoder.output_norm.normalized_shape[0]
        self.dropout = nn.Dropout(float(encoder_kwargs.get("dropout", 0.1)))
        self.classifier = nn.Linear(embedding_dim, num_classes)

    def forward(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> Tensor:
        if attention_mask is None:
            attention_mask = input_ids.ne(0)
        encoded = self.encoder(input_ids, attention_mask)
        pooled = self.encoder.mean_pool(encoded, attention_mask)
        return self.classifier(self.dropout(pooled))

    @torch.no_grad()
    def predict(self, input_ids: Tensor, attention_mask: Tensor | None = None) -> Tensor:
        """評価用に、各クラスの確率を返す。"""
        return self(input_ids, attention_mask).softmax(dim=-1)


class AttentionIntentRouter:
    """小規模な日本語の意図分類器。

    JARVISの起動時に組み込みの例文 (``EXAMPLES``) と、呼び出し側から渡される
    実際の会話結果の蓄積 (``extra_examples``) を合わせて学習し、未知の短い依頼を
    ``operate``、``work``、``chat`` のいずれかに分類します。
    既存の明示的なキーワード判定を置き換えるものではありません。
    """

    LABELS = ("operate", "work", "chat")
    EXAMPLES = {
        "operate": (
            "画面を見て進めて", "ブラウザを操作して", "自分で操作して",
            "画面を開いて確認して", "タブを整理して", "パソコンを操作して",
        ),
        "work": (
            "これを調査して", "コードを修正して", "分析してまとめて",
            "設計して", "レポートを作って", "プログラムを直して",
        ),
        "chat": (
            "こんにちは", "元気ですか", "教えて", "どう思う",
            "今日の気分は", "ありがとう", "何ができますか",
        ),
    }

    def __init__(self, epochs: int = 80, extra_examples: dict[str, list[str]] | None = None) -> None:
        if epochs <= 0:
            raise ValueError("epochs must be positive")
        self.labels = self.LABELS
        training_examples = {label: list(examples) for label, examples in self.EXAMPLES.items()}
        for label, texts in (extra_examples or {}).items():
            if label in training_examples:
                training_examples[label].extend(texts)
        characters = sorted({c for examples in training_examples.values() for text in examples for c in text})
        self.vocabulary = {"<pad>": 0, "<unk>": 1}
        self.vocabulary.update({char: index + 2 for index, char in enumerate(characters)})
        samples = [
            ([self.vocabulary.get(char, 1) for char in text], self.labels.index(label))
            for label, examples in training_examples.items()
            for text in examples
        ]
        max_length = max(len(ids) for ids, _ in samples)
        input_ids = torch.zeros((len(samples), max_length), dtype=torch.long)
        attention_mask = torch.zeros_like(input_ids, dtype=torch.bool)
        targets = torch.tensor([target for _, target in samples], dtype=torch.long)
        for row, (ids, _) in enumerate(samples):
            input_ids[row, :len(ids)] = torch.tensor(ids)
            attention_mask[row, :len(ids)] = True

        torch.manual_seed(0)
        self.model = SelfAttentionClassifier(
            len(self.vocabulary), len(self.labels), embedding_dim=24,
            num_heads=4, num_layers=1, max_length=max_length, dropout=0.0,
        )
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.02)
        loss_fn = nn.CrossEntropyLoss()
        self.model.train()
        for _ in range(epochs):
            optimizer.zero_grad()
            loss = loss_fn(self.model(input_ids, attention_mask), targets)
            loss.backward()
            optimizer.step()
        self.model.eval()
        self.max_length = max_length

    def classify(self, text: str) -> tuple[str, float]:
        """意図と確信度を返す。空文字列は分類しない。"""
        normalized = " ".join(text.split())
        if not normalized:
            raise ValueError("text must not be empty")
        ids = [self.vocabulary.get(char, 1) for char in normalized[:self.max_length]]
        input_ids = torch.tensor([ids], dtype=torch.long)
        mask = torch.ones_like(input_ids, dtype=torch.bool)
        probabilities = self.model.predict(input_ids, mask)[0]
        confidence, index = probabilities.max(dim=0)
        return self.labels[index.item()], float(confidence)
