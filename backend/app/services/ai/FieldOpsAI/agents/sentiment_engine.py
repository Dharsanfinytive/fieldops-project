"""
sentiment_engine.py

Sentiment Analysis Engine for FieldOps Commander.

Responsibilities
----------------
- Analyze customer communication.
- Identify overall sentiment.
- Return a validated SentimentDecision.
- Provide keyword fallback when the AI provider is unavailable.

The Sentiment Engine does NOT:
- Generate customer replies.
- Assign technicians.
- Dispatch jobs.
- Modify database records.
- Send notifications.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

from app.services.ai.FieldOpsAI.runtime.orchestrator import (
    AIOrchestrator,
    ai_orchestrator,
)
from app.services.ai.FieldOpsAI.schemas.ai_task import AITask
from app.services.ai.FieldOpsAI.schemas.sentiment import (
    SentimentContext,
    SentimentDecision,
)

logger = logging.getLogger(__name__)


class SentimentEngine:
    """
    AI engine responsible for customer sentiment analysis.
    """

    def __init__(
        self,
        orchestrator: Optional[AIOrchestrator] = None,
    ) -> None:
        self.orchestrator = orchestrator or ai_orchestrator

    @staticmethod
    def _contains_phrase(message: str, phrase: str) -> bool:
        """Match Latin phrases on token boundaries; retain exact-script matching elsewhere."""
        if not phrase:
            return False
        if any(ord(character) > 127 for character in phrase):
            return phrase in message
        return re.search(
            rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
            message,
            re.IGNORECASE,
        ) is not None

    @classmethod
    def _has_phrase(cls, message: str, phrases) -> bool:
        return any(cls._contains_phrase(message, phrase.lower()) for phrase in phrases)

    @classmethod
    def _safety_indicators(cls, message: str) -> list[str]:
        """Find explicit hazards, ignoring a locally negated phrase only."""
        normalized = message.lower()
        rules = {
            "FIRE": ("fire", "flames", "house is on fire", "தீ", "आग"),
            "ELECTRICAL_SHOCK": (
                "electric shock", "electrical shock", "getting shocked", "shock from the wire",
                "மின்சார அதிர்ச்சி", "மின்சாரம் தாக்க", "बिजली का झटका",
            ),
            "EXPOSED_WIRING": (
                "exposed wire", "live wire", "bare wire", "unsafe wiring", "sparking wire",
                "வெளிப்பட்ட கம்பி", "மின்கம்பி", "खुला तार", "नंगा तार",
            ),
            "SMOKE_OR_BURNING": (
                "burning smell", "smell of burning", "smoke near", "smoke coming", "sparks",
                "எரியும் வாசனை", "புகை", "जलने की गंध", "धुआँ", "चिंगारी",
            ),
            "GAS_LEAK": ("gas leak", "smell of gas", "gas is leaking", "வாயு கசிவு", "गैस रिसाव"),
            "INJURY": ("injured", "injury", "someone got hurt", "hurt by", "காயம்", "चोट"),
            "IMMEDIATE_DANGER": (
                "life threatening", "immediate danger", "unsafe", "dangerous situation",
                "safety issue", "safety problem", "safety concern", "don't feel safe", "do not feel safe", "not safe",
                "seguridad", "பாதுகாப்பு", "सुरक्षा",
                "உடனடி ஆபத்து", "உயிருக்கு ஆபத்து", "तत्काल खतरा", "जान का खतरा",
            ),
            "EMERGENCY": ("emergency", "emergencia", "அவசரம்", "आपातकाल"),
        }
        negations = (
            "no", "not", "never", "without", "isn't", "isnt", "wasn't", "wasnt",
            "aren't", "arent", "there is no", "no hay", "sin", "இல்லை", "அல்ல",
            "नहीं", "मत", "ना",
        )
        detected: list[str] = []
        for category, phrases in rules.items():
            for phrase in phrases:
                pattern = (
                    re.escape(phrase)
                    if any(ord(character) > 127 for character in phrase)
                    else rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
                )
                for match in re.finditer(pattern, normalized):
                    prefix = normalized[:match.start()]
                    clause_start = max(
                        prefix.rfind(mark) for mark in (".", "!", "?", ";", ":")
                    )
                    clause = prefix[clause_start + 1:]
                    for contrast in (" but ", " however ", " though ", " except "):
                        clause = clause.rsplit(contrast, 1)[-1]
                    negated = False
                    if phrase != "not safe":
                        for negation in negations:
                            if cls._contains_phrase(clause, negation):
                                after_negation = clause.rsplit(negation, 1)[-1]
                                if len(after_negation.split()) <= 3:
                                    negated = True
                                    break
                    suffix = normalized[match.end():match.end() + 12]
                    if any(term in suffix for term in (" இல்லை", " அல்ல", " नहीं", " नहीँ")):
                        negated = True
                    if negated:
                        continue
                    detected.append(category)
                    break
                if category in detected:
                    break
        return detected

    # ---------------------------------------------------------
    # Keyword fallback
    # ---------------------------------------------------------

    def _keyword_fallback(
        self,
        context: SentimentContext,
    ) -> SentimentDecision:
        """
        Detect sentiment using keyword rules when the AI
        provider is unavailable.

        Supports:
        - English
        - Spanish
        - Tamil
        - Hindi
        """

        message = context.message.lower().strip()
        safety_indicators = self._safety_indicators(message)

        # -----------------------------------------------------
        # Positive keywords
        # -----------------------------------------------------

        positive_words = (
            # English
            "good",
            "great",
            "excellent",
            "amazing",
            "happy",
            "satisfied",
            "thank you",
            "thanks",
            "appreciate",
            "helpful",
            "perfect",
            "wonderful",
            "fixed the issue",
            "resolved the issue",
            "problem solved",
            "worked well",

            # Spanish
            "bueno",
            "buena",
            "excelente",
            "genial",
            "feliz",
            "satisfecho",
            "satisfecha",
            "gracias",
            "agradecido",
            "agradecida",
            "perfecto",
            "perfecta",

            # Tamil
            "நன்றி",
            "மிகவும் நன்றி",
            "நல்ல",
            "சிறந்த",
            "அருமை",
            "மகிழ்ச்சி",
            "திருப்தி",

            # Hindi
            "धन्यवाद",
            "बहुत अच्छा",
            "अच्छा",
            "अच्छी",
            "शानदार",
            "बेहतरीन",
            "खुश",
            "संतुष्ट",
        )

        # -----------------------------------------------------
        # Negative keywords
        # -----------------------------------------------------

        negative_words = (
            # English
            "bad",
            "terrible",
            "awful",
            "angry",
            "unhappy",
            "dissatisfied",
            "not satisfied",
            "not happy",
            "not pleased",
            "not fixed",
            "not resolved",
            "not good",
            "no satisfecho",
            "no satisfecha",
            "frustrated",
            "disappointed",
            "hate",
            "problem",
            "complaint",
            "broken",
            "failed",
            "not working",
            "worst",
            "poor",
            "late",
            "nobody told me",
            "no one told me",
            "wasn't informed",
            "was not informed",

            # Spanish
            "malo",
            "mala",
            "terrible",
            "horrible",
            "enojado",
            "enojada",
            "insatisfecho",
            "insatisfecha",
            "frustrado",
            "frustrada",
            "decepcionado",
            "decepcionada",
            "problema",
            "queja",
            "roto",
            "no funciona",
            "peor",

            # Tamil
            "மோசம்",
            "மோசமான",
            "கோபம்",
            "வருத்தம்",
            "ஏமாற்றம்",
            "பிரச்சனை",
            "புகார்",
            "வேலை செய்யவில்லை",
            "சரி இல்லை",
            "திருப்தி இல்லை",
            "சரி ஆகவில்லை",

            # Hindi
            "बुरा",
            "बुरी",
            "खराब",
            "भयानक",
            "गुस्सा",
            "नाराज़",
            "असंतुष्ट",
            "निराश",
            "समस्या",
            "शिकायत",
            "काम नहीं कर रहा",
            "संतुष्ट नहीं",
            "सबसे खराब",
        )

        # -----------------------------------------------------
        # Intensifiers
        # -----------------------------------------------------

        intensifiers = (
            # English
            "very",
            "really",
            "extremely",
            "so",
            "highly",
            "absolutely",

            # Spanish
            "muy",
            "realmente",
            "extremadamente",
            "absolutamente",

            # Tamil
            "மிகவும்",
            "ரொம்ப",
            "மிக",

            # Hindi
            "बहुत",
            "बेहद",
            "अत्यंत",
            "बहुत ज्यादा",
        )

        positive_score = 0.0
        negative_score = 0.0

        # -----------------------------------------------------
        # Calculate positive score
        # -----------------------------------------------------

        for word in positive_words:
            if self._contains_phrase(message, word):
                english_negation = re.search(
                    rf"\b(?:not|never|no)\s+(?:very\s+|really\s+)?{re.escape(word)}\b",
                    message,
                )
                script_negation = (
                    re.search(rf"{re.escape(word)}\s*(?:இல்லை|அல்ல|नहीं|नहीँ)", message)
                    if any(ord(character) > 127 for character in word)
                    else None
                )
                if english_negation or script_negation:
                    continue
                positive_score += 1.0

                if self._has_phrase(message, [f"{intensifier} {word}" for intensifier in intensifiers]):
                    positive_score += 0.5

        # -----------------------------------------------------
        # Calculate negative score
        # -----------------------------------------------------

        for word in negative_words:
            if self._contains_phrase(message, word):
                if word in {"bad", "terrible", "awful", "worst", "poor"} and re.search(
                    rf"\bnot\s+(?:very\s+|really\s+)?{re.escape(word)}\b",
                    message,
                ):
                    # Common English litotes should not be counted as a complaint.
                    positive_score += 0.5
                    continue
                negative_score += 1.0

                if self._has_phrase(message, [f"{intensifier} {word}" for intensifier in intensifiers]):
                    negative_score += 0.5

        # Use an explicitly supplied feedback rating as supporting signal.
        # A middle rating stays neutral and does not override the written comment.
        rating_match = re.search(r"customer rating:\s*([1-5])\s*/\s*5", message)
        if rating_match:
            rating = int(rating_match.group(1))
            if rating <= 2:
                negative_score += 1.0
            elif rating >= 4:
                positive_score += 1.0

        # -----------------------------------------------------
        # Determine sentiment
        # -----------------------------------------------------

        if positive_score > 0 and negative_score > 0:
            sentiment = "MIXED"

            confidence = min(
                0.95,
                0.70
                + min(
                    positive_score,
                    negative_score,
                )
                * 0.08,
            )

        elif positive_score > 0:
            sentiment = "POSITIVE"

            confidence = min(
                0.95,
                0.70 + positive_score * 0.08,
            )

        elif negative_score > 0:
            sentiment = "NEGATIVE"

            confidence = min(
                0.95,
                0.70 + negative_score * 0.08,
            )

        else:
            sentiment = "NEUTRAL"
            confidence = 0.50

        confidence = round(confidence, 2)

        # -----------------------------------------------------
        # Determine emotion
        # -----------------------------------------------------

        anger_words = (
            "angry",
            "enojado",
            "enojada",
            "கோபம்",
            "गुस्सा",
            "नाराज़",
        )

        disappointment_words = (
            "disappointed",
            "decepcionado",
            "decepcionada",
            "ஏமாற்றம்",
            "निराश",
        )

        if sentiment == "POSITIVE":
            emotion = "HAPPY"

        elif sentiment == "NEGATIVE":
            if any(
                self._contains_phrase(message, word)
                for word in anger_words
            ):
                emotion = "ANGRY"

            elif any(
                self._contains_phrase(message, word)
                for word in disappointment_words
            ):
                emotion = "DISAPPOINTED"

            else:
                emotion = "FRUSTRATED"

        elif sentiment == "MIXED":
            emotion = "CONCERNED"

        else:
            emotion = "CALM"

        if safety_indicators and emotion in {"CALM", "CONFUSED"}:
            emotion = "CONCERNED"
        elif self._has_phrase(message, ("afraid", "scared", "worried", "anxious", "பயம்", "கவலை", "डर", "चिंतित")):
            emotion = "ANXIOUS"

        # -----------------------------------------------------
        # Determine urgency
        # -----------------------------------------------------

        high_urgency_keywords = (
            # English
            "manager",
            "supervisor",
            "escalate",
            "escalation",
            "cancel",
            "cancellation",
            "broken",
            "not working",
            "still not fixed",
            "multiple complaints",

            # Spanish
            "gerente",
            "supervisor",
            "escalar",
            "cancelar",
            "no funciona",

            # Tamil
            "மேலாளர்",
            "புகார்",
            "ரத்து",
            "வேலை செய்யவில்லை",

            # Hindi
            "मैनेजर",
            "प्रबंधक",
            "एस्केलेट",
            "रद्द",
            "काम नहीं कर रहा",
        )

        medium_urgency_keywords = (
            "problem",
            "issue",
            "complaint",
            "frustrated",
            "disappointed",
            "problema",
            "queja",
            "பிரச்சனை",
            "புகார்",
            "समस्या",
            "शिकायत",
        )

        critical_urgency_keywords = ("critical danger", "urgent safety issue")

        critical_safety_indicators = {
            "FIRE", "ELECTRICAL_SHOCK", "EXPOSED_WIRING", "GAS_LEAK",
            "INJURY", "EMERGENCY",
        }
        if (
            any(indicator in critical_safety_indicators for indicator in safety_indicators)
            or self._has_phrase(message, critical_urgency_keywords)
        ):
            urgency = "CRITICAL"

        elif safety_indicators:
            urgency = "HIGH"

        elif any(
            self._contains_phrase(message, keyword)
            for keyword in high_urgency_keywords
        ):
            urgency = "HIGH"

        elif any(
            self._contains_phrase(message, keyword)
            for keyword in medium_urgency_keywords
        ):
            urgency = "MEDIUM"

        else:
            urgency = "LOW"

        # -----------------------------------------------------
        # Determine human intervention
        # -----------------------------------------------------

        human_intervention_keywords = (
            "manager",
            "supervisor",
            "escalate",
            "escalation",
            "legal",
            "lawyer",
            "safety",
            "danger",
            "emergency",

            "gerente",
            "supervisor",
            "escalar",

            "மேலாளர்",
            "பாதுகாப்பு",
            "அவசரம்",

            "मैनेजर",
            "सुरक्षा",
            "आपातकाल",
        )

        requires_human = (
            bool(safety_indicators)
            or self._has_phrase(message, human_intervention_keywords)
            or emotion == "ANGRY"
            or (
                sentiment == "NEGATIVE"
                and confidence < 0.70
            )
        )

        # -----------------------------------------------------
        # Factual summary
        # -----------------------------------------------------

        if sentiment == "POSITIVE":
            summary = (
                "Customer expresses positive feedback "
                "about the service."
            )

        elif sentiment == "NEGATIVE":
            summary = (
                "Customer expresses dissatisfaction "
                "or reports a service problem."
            )

        elif sentiment == "MIXED":
            summary = (
                "Customer expresses both positive and "
                "negative feelings about the service."
            )

        else:
            summary = (
                "Customer provides factual or emotionally "
                "neutral information."
            )

        return SentimentDecision(
            sentiment=sentiment,
            emotion=emotion,
            urgency=urgency,
            severity=(
                "CRITICAL" if safety_indicators or urgency == "CRITICAL"
                else "HIGH" if self._has_phrase(message, ("third time", "multiple unresolved", "injured", "injury", "unsafe", "still not fixed"))
                else "MEDIUM" if sentiment in {"NEGATIVE", "MIXED"}
                else "LOW"
            ),
            issue_categories=self._classify_issue_categories(message),
            requires_human=requires_human,
            confidence=confidence,
            summary=summary,
            recommended_action=(
                "Immediately route this safety report to an authorized human for review."
                if safety_indicators
                else "Review the customer's message and contact them with an update."
                if sentiment in {"NEGATIVE", "MIXED"} or requires_human
                else "No follow-up is indicated by this message."
            ),
            analysis_source="KEYWORD_FALLBACK",
        )

    @classmethod
    def _classify_issue_categories(cls, message: str) -> list[str]:
        categories = []
        rules = (
            ("SERVICE_DELAY", ("late", "waiting", "not arrived", "delay", "waited for hours", "technician has not arrived")),
            ("SUPPORT_NON_RESPONSE", ("no response", "not responding", "nobody is responding", "no update", "nobody told me", "no one told me", "wasn't informed", "was not informed")),
            ("TECHNICIAN_BEHAVIOR", ("rude", "unprofessional", "technician shouted", "technician was rude")),
            ("SERVICE_QUALITY", ("poor service", "bad service", "did not fix", "not fixed", "incomplete work")),
            ("SCHEDULING", ("appointment", "reschedule", "schedule")),
            ("PRICING_BILLING", ("price", "bill", "invoice", "charge", "cost", "incorrect amount")),
            ("PAYMENT", ("payment failed", "charged twice", "refund", "payment")),
            ("EQUIPMENT_DAMAGE", ("damaged", "damage", "broke my", "broken by the technician")),
            ("UNRESOLVED_ISSUE", ("still broken", "still not fixed", "unresolved", "issue remains", "problem still exists")),
            ("REPEATED_COMPLAINT", ("complained twice", "third time", "again and again", "multiple complaints", "already complained")),
            ("SAFETY", tuple(phrase for phrases in (
                ("electric shock", "electrical shock", "getting shocked", "shock from the wire", "மின்சார அதிர்ச்சி", "மின்சாரம் தாக்க", "बिजली का झटका"),
                ("fire", "flames", "house is on fire", "தீ", "आग"),
                ("exposed wire", "live wire", "bare wire", "unsafe wiring", "sparks", "gas leak", "burning smell", "unsafe", "danger", "safety"),
            ) for phrase in phrases)),
        )
        for category, terms in rules:
            if category == "SAFETY":
                if cls._safety_indicators(message):
                    categories.append(category)
            elif cls._has_phrase(message, terms):
                categories.append(category)
        if not categories and cls._has_phrase(message, ("great", "thanks", "thank you", "excellent", "customer rating:")):
            categories.append("GENERAL_FEEDBACK")
        return categories[:5]

    # ---------------------------------------------------------
    # Main analysis
    # ---------------------------------------------------------

    def analyze(
        self,
        context: SentimentContext,
    ) -> SentimentDecision:
        """
        Analyze customer communication using the AI
        orchestrator with keyword fallback.
        """

        start_time = time.perf_counter()

        logger.info(
            "Sentiment Engine started."
        )

        try:
            # -------------------------------------------------
            # Primary path: AI through orchestrator
            # -------------------------------------------------

            decision = self.orchestrator.execute(
                task=AITask.SENTIMENT,
                context=context.model_dump(),
                response_schema=SentimentDecision,
            )

            # Safety detection is independent of the model's sentiment and
            # confidence. An explicit hazard always requires a human review.
            indicators = self._safety_indicators(context.message.lower())
            if indicators:
                categories = list(decision.issue_categories or [])
                if "SAFETY" not in categories:
                    categories.append("SAFETY")
                decision = decision.model_copy(update={
                    "urgency": "CRITICAL",
                    "severity": "CRITICAL",
                    "requires_human": True,
                    "emotion": "CONCERNED" if decision.emotion in {"CALM", "CONFUSED"} else decision.emotion,
                    "issue_categories": categories[:5],
                    "recommended_action": "Immediately route this safety report to an authorized human for review.",
                    "analysis_source": "AI",
                })

            elapsed = time.perf_counter() - start_time

            logger.info(
                "Sentiment completed in %.2f sec | "
                "Sentiment=%s | Confidence=%.2f",
                elapsed,
                decision.sentiment,
                decision.confidence,
            )

            return decision

        except Exception as exc:
            # -------------------------------------------------
            # Fallback path
            # -------------------------------------------------

            logger.exception(
                "AI sentiment analysis failed. "
                "Using keyword fallback. Error=%s",
                exc,
            )

            fallback_result = self._keyword_fallback(
                context
            )

            logger.info(
                "Sentiment keyword fallback completed. "
                "Sentiment=%s | Confidence=%.2f",
                fallback_result.sentiment,
                fallback_result.confidence,
            )

            return fallback_result
