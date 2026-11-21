package com.vaada.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import com.vaada.models.AudioCaptureMetadata
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

class AudioCaptureManager(private val context: Context) {

    companion object {
        private const val TAG = "AudioCaptureManager"
        const val SAMPLE_RATE = 16000 // 16 kHz
        const val CHANNEL_CONFIG = AudioFormat.CHANNEL_IN_MONO
        const val AUDIO_FORMAT = AudioFormat.ENCODING_PCM_16BIT

        // Silence & Endpointing thresholds (Adaptive for noisy kirana shop)
        private const val MIN_SPEECH_DURATION_MS = 600L      // Must speak for at least 600ms before silence triggers
        private const val SILENCE_TIMEOUT_MS = 1400L         // 1.4s of trailing silence completes the utterance
        private const val MAX_RECORDING_DURATION_MS = 8000L  // 8.0s hard safety cap
        private const val BASE_SPEECH_THRESHOLD = 0.015f     // Minimum baseline speech RMS threshold
        private const val NOISE_MARGIN_RMS = 0.015f          // Required elevation above estimated ambient noise floor
    }

    private var audioRecord: AudioRecord? = null
    private var recordingJob: Job? = null
    private val isRecording = AtomicBoolean(false)

    var onAmplitudeUpdate: ((Float) -> Unit)? = null
    var onCaptureCompleted: ((AudioCaptureMetadata, File?) -> Unit)? = null
    var onError: ((String) -> Unit)? = null

    val isCapturing: Boolean
        get() = isRecording.get()

    @SuppressLint("MissingPermission")
    fun startCapture(scope: CoroutineScope, handoffStartTimeMs: Long? = null) {
        val tCaptureInitStart = System.currentTimeMillis()
        if (isRecording.get()) {
            Log.w(TAG, "Capture already in progress")
            return
        }

        val minBufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL_CONFIG, AUDIO_FORMAT)
        if (minBufferSize == AudioRecord.ERROR || minBufferSize == AudioRecord.ERROR_BAD_VALUE) {
            val error = "Unable to determine minimum buffer size for AudioRecord"
            Log.e(TAG, error)
            onError?.invoke(error)
            return
        }

        val bufferSize = max(minBufferSize * 2, 4096)

        try {
            audioRecord = AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION,
                SAMPLE_RATE,
                CHANNEL_CONFIG,
                AUDIO_FORMAT,
                bufferSize
            )

            if (audioRecord?.state != AudioRecord.STATE_INITIALIZED) {
                val error = "AudioRecord failed to initialize (state != STATE_INITIALIZED)"
                Log.e(TAG, error)
                releaseAudioRecord()
                onError?.invoke(error)
                return
            }

            val tBeforeStart = System.currentTimeMillis()
            audioRecord?.startRecording()
            val tRecordStarted = System.currentTimeMillis()
            isRecording.set(true)

            Log.d(
                "HandoffProfiler",
                "AudioCaptureManager: AudioRecord created in ${tBeforeStart - tCaptureInitStart}ms, startRecording took ${tRecordStarted - tBeforeStart}ms (Total T2->T3: ${tRecordStarted - tCaptureInitStart}ms)"
            )

            recordingJob = scope.launch(Dispatchers.IO) {
                runCaptureLoop(bufferSize, handoffStartTimeMs, tRecordStarted)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start AudioRecord capture", e)
            releaseAudioRecord()
            isRecording.set(false)
            onError?.invoke(e.message ?: "Unknown audio recording error")
        }
    }

    private fun runCaptureLoop(bufferSize: Int, handoffStartTimeMs: Long? = null, tRecordStarted: Long = 0L) {
        val audioData = ShortArray(bufferSize / 2)
        val byteStream = ByteArrayOutputStream()
        val startTime = System.currentTimeMillis()
        var lastSpeechTime = startTime
        var speechDetected = false
        var maxAmplitude = 0
        var totalSquareSum = 0.0
        var totalSamples = 0L
        var isFirstFrame = true

        // Adaptive ambient noise floor tracker
        var estimatedNoiseFloor = BASE_SPEECH_THRESHOLD
        var frameCount = 0

        try {
            while (isRecording.get()) {
                val now = System.currentTimeMillis()
                val elapsedMs = now - startTime

                // Hard safety timeout
                if (elapsedMs >= MAX_RECORDING_DURATION_MS) {
                    Log.i(TAG, "Capture reached MAX_RECORDING_DURATION_MS ($MAX_RECORDING_DURATION_MS ms). Stopping.")
                    break
                }

                val readShorts = audioRecord?.read(audioData, 0, audioData.size) ?: -1
                if (readShorts > 0) {
                    if (isFirstFrame) {
                        isFirstFrame = false
                        val tFirstAudioRead = System.currentTimeMillis()
                        val recordToReadMs = if (tRecordStarted > 0) tFirstAudioRead - tRecordStarted else 0L
                        if (handoffStartTimeMs != null) {
                            val totalHandoffMs = tFirstAudioRead - handoffStartTimeMs
                            Log.i(
                                "HandoffProfiler",
                                ">>> HANDOFF METRICS COMPLETE: T0->T4 Total = ${totalHandoffMs}ms (RecordStart->FirstRead: ${recordToReadMs}ms, Frame: ${readShorts} samples) <<<"
                            )
                        } else {
                            Log.i("HandoffProfiler", "PTT First audio frame read in ${recordToReadMs}ms (${readShorts} samples)")
                        }
                    }

                    var chunkSquareSum = 0.0
                    for (i in 0 until readShorts) {
                        val sample = audioData[i]
                        val absSample = Math.abs(sample.toInt())
                        if (absSample > maxAmplitude) {
                            maxAmplitude = absSample
                        }
                        chunkSquareSum += (sample * sample)

                        // Convert short to little-endian bytes for PCM stream
                        byteStream.write(sample.toInt() and 0xFF)
                        byteStream.write((sample.toInt() shr 8) and 0xFF)
                    }

                    totalSquareSum += chunkSquareSum
                    totalSamples += readShorts

                    val chunkRms = sqrt(chunkSquareSum / readShorts).toFloat()
                    val normalizedRms = min(chunkRms / 32767f, 1.0f)
                    onAmplitudeUpdate?.invoke(normalizedRms)

                    // Adaptive ambient noise floor estimation
                    frameCount++
                    if (frameCount <= 3) {
                        estimatedNoiseFloor = if (frameCount == 1) normalizedRms else min(estimatedNoiseFloor, normalizedRms)
                    } else if (normalizedRms < estimatedNoiseFloor) {
                        estimatedNoiseFloor = estimatedNoiseFloor * 0.85f + normalizedRms * 0.15f
                    } else if (!speechDetected) {
                        estimatedNoiseFloor = estimatedNoiseFloor * 0.995f + normalizedRms * 0.005f
                    }

                    val adaptiveSpeechThreshold = max(BASE_SPEECH_THRESHOLD, estimatedNoiseFloor + NOISE_MARGIN_RMS)

                    // Voice Activity Detection & Endpointing logic
                    if (normalizedRms >= adaptiveSpeechThreshold) {
                        speechDetected = true
                        lastSpeechTime = now
                    } else if (speechDetected) {
                        val silenceDurationMs = now - lastSpeechTime
                        val spokenDurationMs = now - startTime
                        if (silenceDurationMs >= SILENCE_TIMEOUT_MS && spokenDurationMs >= MIN_SPEECH_DURATION_MS) {
                            Log.i(
                                TAG,
                                "Silence endpoint detected ($silenceDurationMs ms silence after speech; noise floor=%.4f, thresh=%.4f). Stopping capture.".format(
                                    estimatedNoiseFloor,
                                    adaptiveSpeechThreshold
                                )
                            )
                            break
                        }
                    }
                } else if (readShorts == AudioRecord.ERROR_INVALID_OPERATION) {
                    Log.e(TAG, "AudioRecord read returned ERROR_INVALID_OPERATION")
                    break
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Exception during audio capture loop", e)
        } finally {
            val durationMs = System.currentTimeMillis() - startTime
            val finalBytes = byteStream.toByteArray()
            val avgRms = if (totalSamples > 0) {
                (sqrt(totalSquareSum / totalSamples) / 32767.0).toFloat()
            } else {
                0f
            }

            // Save utterance to local cache file for inspection
            val outputFile = try {
                val file = File(context.cacheDir, "last_utterance.pcm")
                FileOutputStream(file).use { it.write(finalBytes) }
                file
            } catch (e: Exception) {
                Log.w(TAG, "Failed to persist PCM utterance to cache file", e)
                null
            }

            val metadata = AudioCaptureMetadata(
                sampleRate = SAMPLE_RATE,
                channels = 1,
                audioFormat = "PCM_16BIT",
                durationMs = durationMs,
                totalBytes = finalBytes.size,
                maxAmplitude = maxAmplitude,
                averageRms = avgRms
            )

            isRecording.set(false)
            releaseAudioRecord()

            onCaptureCompleted?.invoke(metadata, outputFile)
        }
    }

    fun stopCapture() {
        if (!isRecording.get()) return
        isRecording.set(false)
        recordingJob?.cancel()
        recordingJob = null
    }

    private fun releaseAudioRecord() {
        try {
            if (audioRecord?.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                audioRecord?.stop()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error stopping AudioRecord", e)
        } finally {
            try {
                audioRecord?.release()
            } catch (e: Exception) {
                Log.w(TAG, "Error releasing AudioRecord", e)
            }
            audioRecord = null
        }
    }

    fun release() {
        stopCapture()
        releaseAudioRecord()
    }
}
