"""İadeAjan domain modelleri — risk, belge, aksiyon ve netleştirme şemaları."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.penalty_codes import PenaltyCode

_NEGATIVE_SCORE_MSG = "score_impact 0'dan küçük olmalıdır"


def _validate_negative_score_impact(value: int) -> int:
    if value >= 0:
        raise ValueError(_NEGATIVE_SCORE_MSG)
    return value


class RiskItem(BaseModel):
    """Analiz sırasında tespit edilen tek bir risk maddesi."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., description="Risk başlığı")
    severity: Literal["kritik", "yüksek", "orta", "düşük"] = Field(
        ..., description="Risk şiddet seviyesi"
    )
    reason: str = Field(..., description="Riskin gerekçesi")
    score_impact: int = Field(..., description="Skora etkisi (negatif tam sayı)")
    suggested_action: str | None = Field(
        default=None, description="Önerilen düzeltme aksiyonu"
    )
    code: PenaltyCode | None = Field(
        default=None, description="Ceza kanunnamesi kodu (PENALTY_MATRIX)"
    )
    count: int = Field(default=1, ge=1, description="Etkilenen fatura/kayıt adedi")
    invoice_ids: list[str] = Field(
        default_factory=list, description="Etkilenen fatura ID listesi"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Ek bağlam (tutar farkı, dönem vb.)"
    )
    source: Literal["python", "llm", "hybrid", "fallback"] = Field(
        default="python", description="Cezayı üreten katman"
    )

    @field_validator("score_impact")
    @classmethod
    def validate_score_impact(cls, value: int) -> int:
        return _validate_negative_score_impact(value)


class MissingDoc(BaseModel):
    """Dosyada eksik veya bulunamayan zorunlu/opsiyonel belge kaydı."""

    model_config = ConfigDict(extra="forbid")

    doc_name: str = Field(..., description="Eksik belge adı")
    reason: str = Field(..., description="Eksikliğin açıklaması")
    score_impact: int = Field(..., description="Skora etkisi (negatif tam sayı)")
    required: bool = Field(default=True, description="Belgenin zorunlu olup olmadığı")
    code: PenaltyCode | None = Field(
        default=None, description="Ceza kanunnamesi kodu"
    )

    @field_validator("score_impact")
    @classmethod
    def validate_score_impact(cls, value: int) -> int:
        return _validate_negative_score_impact(value)


class PenaltyDetection(BaseModel):
    """LLM structured output şeması — tek bir ceza tespiti."""

    model_config = ConfigDict(extra="ignore")

    code: PenaltyCode = Field(..., description="Kanunnamedeki ceza kodu")
    evidence: str = Field(
        ..., max_length=200, description="Kısa Türkçe kanıt açıklaması (max 200 karakter)"
    )
    invoice_ids: list[str] = Field(
        default_factory=list, description="İlgili fatura numaraları"
    )
    count: int = Field(
        default=1, ge=1, le=100, description="Etkilenen kayıt adedi (DQ_* için)"
    )
    macro_flag: bool = Field(
        default=False,
        description=(
            "Tekil fatura hatası değil, dosya geneline yayılan şüpheli pattern; "
            "Shadow Learning önceliği için."
        ),
    )


class LLMAnalysisResult(BaseModel):
    """LLM'den dönen tam analiz sonucu."""

    model_config = ConfigDict(extra="ignore")

    summary: str = Field(
        default="", description="Dosya hakkında 2 cümlelik AI yorumu"
    )
    penalties: list[PenaltyDetection] = Field(
        default_factory=list, description="Tespit edilen cezalar"
    )


DocumentClassificationType = Literal[
    "gumruk_beyannamesi",
    "ymm_tasdik_raporu",
    "2no_kdv_beyannamesi",
    "diger",
    "bilinmiyor",
]


class DocumentClassification(BaseModel):
    """LLM belge sınıflandırması — Analyzer envanterine düşük güvenle yazılmaz."""

    model_config = ConfigDict(extra="ignore")

    file_name: str = Field(..., description="Sınıflandırılan dosya adı")
    type: DocumentClassificationType = Field(
        ...,
        description="Belge türü (envanter type anahtarıyla uyumlu)",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Model güveni (≥0.8 ise envantere eklenir)",
    )
    evidence: str = Field(
        ...,
        max_length=300,
        description="Belgede görülen kısa kanıt (Türkçe)",
    )


class ActionCard(BaseModel):
    """Skoru artırmak için önerilen kullanıcı aksiyonu."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., description="Aksiyon başlığı")
    description: str = Field(..., description="Aksiyonun detaylı açıklaması")
    score_impact: str = Field(
        ..., description="Tahmini skor etkisi (örn: '+8')"
    )
    effort: str = Field(..., description="Tahmini efor/süre (örn: '1 gün')")

    @field_validator("score_impact")
    @classmethod
    def validate_score_impact(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("score_impact boş olamaz")
        return normalized


class FinancePrecheck(BaseModel):
    """Ön finansman değerlendirme sonucu."""

    model_config = ConfigDict(extra="forbid")

    eligible: bool = Field(..., description="Ön finansmana uygunluk durumu")
    estimated_limit: float = Field(
        default=0.0, description="Tahmini finansman limiti (TL)"
    )
    advance_ratio: float = Field(
        default=0.0, description="Avans oranı (0.0–1.0 arası)"
    )
    advance_amount: float = Field(
        default=0.0, description="Tahmini avans tutarı (TL)"
    )
    message: str = Field(..., description="Kullanıcıya gösterilecek özet mesaj")
    reference_code: str | None = Field(
        default=None, description="Ön değerlendirme referans kodu"
    )
    validity_days: int = Field(
        default=15, description="Ön değerlendirmenin geçerlilik süresi (gün)"
    )

    @field_validator("estimated_limit", "advance_amount")
    @classmethod
    def validate_non_negative_amounts(cls, value: float) -> float:
        if value < 0:
            raise ValueError("Bu alan negatif olamaz")
        return value

    @field_validator("advance_ratio")
    @classmethod
    def validate_advance_ratio(cls, value: float) -> float:
        if value < 0:
            raise ValueError("advance_ratio negatif olamaz")
        if value > 1.0:
            raise ValueError("advance_ratio 0.0 ile 1.0 arasında olmalıdır")
        return value

    @field_validator("validity_days")
    @classmethod
    def validate_validity_days(cls, value: int) -> int:
        if value < 1:
            raise ValueError("validity_days en az 1 olmalıdır")
        return value


class ClarificationQuestion(BaseModel):
    """Kullanıcıdan netleştirme için sorulacak hedefli soru."""

    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(..., description="Benzersiz soru kimliği")
    question_text: str = Field(..., description="Kullanıcıya gösterilecek soru metni")
    field: str = Field(..., description="Güncellenecek state alan adı")
    type: Literal["boolean", "choice", "text"] = Field(
        ..., description="Soru tipi"
    )
    options: list[str] = Field(
        default_factory=list, description="Seçenek listesi (choice/boolean için)"
    )
    required: bool = Field(default=True, description="Sorunun zorunlu olup olmadığı")
    reason: str = Field(..., description="Sorunun sorulma gerekçesi")
    score_effect: dict[str, int] = Field(
        default_factory=dict,
        description="Cevap anahtarına göre skor etkisi eşlemesi",
    )

    @field_validator("question_id")
    @classmethod
    def validate_question_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question_id boş olamaz")
        return value.strip()

    @model_validator(mode="after")
    def validate_options_for_type(self) -> Self:
        if self.type == "choice" and not self.options:
            raise ValueError("choice tipi sorularda options boş olamaz")

        if self.type == "boolean" and self.options:
            normalized = [option.strip().lower() for option in self.options]
            yes_tokens = {"evet", "yes", "true", "var"}
            no_tokens = {"hayır", "hayir", "no", "false", "yok"}
            has_yes = any(token in option for option in normalized for token in yes_tokens)
            has_no = any(token in option for option in normalized for token in no_tokens)
            if not (has_yes and has_no) and len(self.options) < 2:
                raise ValueError(
                    "boolean tipi sorularda options anlamlı seçenekler içermelidir "
                    "(örn: ['Evet', 'Hayır'])"
                )
            if len(self.options) > 6:
                raise ValueError("boolean tipi sorularda en fazla 6 seçenek olabilir")

        return self


class CriticFeedback(BaseModel):
    """Critic (Denetmen) ajanın, Analyzer ajanın bulgularına yaptığı öz denetim geri bildirimi."""

    model_config = ConfigDict(extra="forbid")

    is_approved: bool = Field(..., description="Denetmen ajan onayladı mı?")
    feedback: str = Field(..., description="Eleştiri metni veya onay mesajı")
    suggested_correction: str | None = Field(
        default=None,
        description="Onaylanmadıysa önerilen düzeltme",
    )


if __name__ == "__main__":
    from app.schemas.models import RiskItem
    from app.core.state import IadeAjanState

    r = RiskItem(
        title="Test",
        severity="yüksek",
        reason="Test reason",
        score_impact=-10,
    )
    print("✅ models.py OK:", r.model_dump())
    print("✅ state.py OK: IadeAjanState yüklendi")
