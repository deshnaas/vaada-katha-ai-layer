package com.vaada.config

/**
 * Languages the app offers for speech input.
 *
 * ROOT CAUSE of the old language problem: the language was a hard-coded string ("hi-IN,en-IN") buried in the audio
 * service, [com.vaada.models.MerchantProfile.language] was never read, and there was no way to change or persist it.
 *
 * What the gateway really supports (backend/voice_gateway/gnani_stt.py): STT accepts the codes in
 * [GATEWAY_STT_CODES] (1-4 comma separated, or "indic-auto"). BUT the dialogue/intent layer that turns the
 * transcript into a ledger action only understands Hindi/Hinglish (Latin transliteration) and English; Gnani is asked
 * for the "transliterate" format. Offering Tamil/Marathi/etc. here would transcribe fine and then fail to understand
 * the sentence, so only the three modes that work end to end are offered. Replies are Hinglish text in every mode.
 */
enum class VoiceLanguage(val code: String, val label: String, val hint: String) {
    HINDI_ENGLISH("hi-IN,en-IN", "हिन्दी + English", "Hinglish (recommended)"),
    HINDI("hi-IN", "हिन्दी", "Hindi only"),
    ENGLISH("en-IN", "English", "English only");

    companion object {
        val DEFAULT = HINDI_ENGLISH

        /** Codes the gateway accepts for speech-to-text (gnani_stt.SUPPORTED_CODES). */
        val GATEWAY_STT_CODES = setOf(
            "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "pa-IN", "ta-IN", "te-IN"
        )

        fun fromCode(code: String?): VoiceLanguage? = entries.firstOrNull { it.code == code }

        /** Mirrors the gateway's own rule so a bad value is rejected on the device, not with an HTTP 422. */
        fun isValidGatewayValue(value: String): Boolean {
            if (value == "indic-auto") return true
            val parts = value.split(",")
            return parts.size in 1..4 && parts.all { it in GATEWAY_STT_CODES } && parts.toSet().size == parts.size
        }
    }
}
