package com.vaada.models

/**
 * Single source of truth for the Assistant lifecycle and audio states.
 */
sealed interface AssistantState {
    data object Stopped : AssistantState
    data object Standby : AssistantState
    data class Listening(val amplitudeRms: Float = 0f) : AssistantState
    data class Captured(val metadata: AudioCaptureMetadata) : AssistantState
    /** Gateway call in progress; audio already captured and released. */
    data object Processing : AssistantState
    /** Gateway returned a reply. Shown until next wake-word cycle. */
    data class Responding(
        val transcript: String,
        val replyText: String,
        val replyKind: String,
        val dialogueState: String?,
        val ledgerCommitted: Boolean,
        val duplicate: Boolean,
        /** Pending action reported by the gateway (dialogue.pending). Display only; the gateway owns the truth. */
        val pendingCustomer: String? = null,
        val pendingAmountPaise: Long? = null,
        val pendingType: String? = null,
        val awaiting: String? = null,
        val notice: String? = null
    ) : AssistantState
    data class Error(val message: String) : AssistantState
}

/**
 * Metadata recorded during an utterance capture session.
 */
data class AudioCaptureMetadata(
    val sampleRate: Int = 16000,
    val channels: Int = 1,
    val audioFormat: String = "PCM_16BIT",
    val durationMs: Long,
    val totalBytes: Int,
    val maxAmplitude: Int = 0,
    val averageRms: Float = 0f,
    val timestamp: Long = System.currentTimeMillis()
)
