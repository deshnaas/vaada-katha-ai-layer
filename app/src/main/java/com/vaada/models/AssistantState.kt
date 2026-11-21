package com.vaada.models

/**
 * Single source of truth for the Assistant lifecycle and audio states.
 */
sealed interface AssistantState {
    data object Stopped : AssistantState
    data object Standby : AssistantState
    data class Listening(val amplitudeRms: Float = 0f) : AssistantState
    data class Captured(val metadata: AudioCaptureMetadata) : AssistantState
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
