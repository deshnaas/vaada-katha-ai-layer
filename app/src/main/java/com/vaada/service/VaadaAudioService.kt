package com.vaada.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.media.MediaPlayer
import android.util.Base64
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.suxsem.androidwakeword.WakeWordDetector
import com.vaada.audio.AudioCaptureManager
import com.vaada.network.VoiceAuthClient
import com.vaada.network.VoiceGatewayClient
import com.vaada.models.VoiceAuthSession
import java.io.File
import java.io.FileOutputStream
import java.util.UUID
import com.vaada.models.AssistantState
import com.vaada.models.AudioCaptureMetadata
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import java.util.concurrent.atomic.AtomicBoolean

class VaadaAudioService : Service() {

    companion object {
        private const val TAG = "VaadaAudioService"

        const val ACTION_START_SERVICE = "com.vaada.action.START_SERVICE"
        const val ACTION_STOP_SERVICE = "com.vaada.action.STOP_SERVICE"
        const val ACTION_START_RECORDING = "com.vaada.action.START_RECORDING"
        const val ACTION_STOP_RECORDING = "com.vaada.action.STOP_RECORDING"

        private val _assistantState = MutableStateFlow<AssistantState>(AssistantState.Stopped)
        val assistantState: StateFlow<AssistantState> = _assistantState.asStateFlow()

        private val _lastCapturedMetadata = MutableStateFlow<AudioCaptureMetadata?>(null)
        val lastCapturedMetadata: StateFlow<AudioCaptureMetadata?> = _lastCapturedMetadata.asStateFlow()

        private val _wakeWordDetectionCount = MutableStateFlow(0)
        val wakeWordDetectionCount: StateFlow<Int> = _wakeWordDetectionCount.asStateFlow()

        private val _lastWakeWordScore = MutableStateFlow(0f)
        val lastWakeWordScore: StateFlow<Float> = _lastWakeWordScore.asStateFlow()

        // ── Wake-Word & Handoff Configuration (Centralized & Tunable) ────────────────
        const val DEFAULT_WAKE_WORD_THRESHOLD = 0.50f  // Configured threshold (tested on CMF Phone 2 Pro)
        const val HANDOFF_SAFETY_DELAY_MS = 20L        // Measured buffer between detector pause and AudioRecord start (reduced from 60ms)

        private val _wakeWordThreshold = MutableStateFlow(DEFAULT_WAKE_WORD_THRESHOLD)
        val wakeWordThreshold: StateFlow<Float> = _wakeWordThreshold.asStateFlow()

        private val _lastEvaluatedScore = MutableStateFlow(0f)
        val lastEvaluatedScore: StateFlow<Float> = _lastEvaluatedScore.asStateFlow()

        private val _peakScore = MutableStateFlow(0f)
        val peakScore: StateFlow<Float> = _peakScore.asStateFlow()

        private var peakDecayJob: Job? = null

        // Merchant login token is held in memory only and supplied by MainActivity.
        @Volatile private var merchantBearerToken: String? = null

        fun setMerchantToken(token: String?) {
            merchantBearerToken = token?.takeIf { it.isNotBlank() }
        }

        // ── Voice Auth Session (in-memory, never persisted) ─────────────────
        private val _voiceAuthSession = MutableStateFlow<VoiceAuthSession>(VoiceAuthSession.None)
        val voiceAuthSession: StateFlow<VoiceAuthSession> = _voiceAuthSession.asStateFlow()

        /**
         * Reset voice auth session. MUST be called on logout or account switch
         * so one merchant's session is never reused for another.
         */
        fun resetVoiceAuth() {
            _voiceAuthSession.value = VoiceAuthSession.None
        }

        /** Update voice auth session (called from MainActivity after verify/logout). */
        fun setVoiceAuthSession(session: VoiceAuthSession) {
            _voiceAuthSession.value = session
        }

        fun startService(context: Context) {
            val intent = Intent(context, VaadaAudioService::class.java).apply {
                action = ACTION_START_SERVICE
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stopService(context: Context) {
            val intent = Intent(context, VaadaAudioService::class.java).apply {
                action = ACTION_STOP_SERVICE
            }
            context.startService(intent)
        }

        fun startRecording(context: Context) {
            val intent = Intent(context, VaadaAudioService::class.java).apply {
                action = ACTION_START_RECORDING
            }
            context.startService(intent)
        }

        fun stopRecording(context: Context) {
            val intent = Intent(context, VaadaAudioService::class.java).apply {
                action = ACTION_STOP_RECORDING
            }
            context.startService(intent)
        }
    }

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var wakeLock: PowerManager.WakeLock? = null
    private lateinit var audioCaptureManager: AudioCaptureManager

    // Wake Word Engine
    private var wakeWordDetector: WakeWordDetector? = null
    private var wakeWordJob: Job? = null

    // Voice Gateway
    private val voiceGatewayClient = VoiceGatewayClient()
    private val voiceAuthClient = VoiceAuthClient()
    private val sessionId: String = UUID.randomUUID().toString()

    // Concurrency & Handoff guards
    private val isServiceRunning = AtomicBoolean(false)
    private val isHandoffInProgress = AtomicBoolean(false)
    private val isManualRecordingActive = AtomicBoolean(false)
    private val isConversationActive = AtomicBoolean(false)

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "VaadaAudioService onCreate")
        AudioNotification.createNotificationChannel(this)

        val powerManager = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "Vaada:AudioServiceWakeLock").apply {
            setReferenceCounted(false)
        }

        audioCaptureManager = AudioCaptureManager(this).apply {
            onAmplitudeUpdate = { normalizedRms ->
                _assistantState.value = AssistantState.Listening(normalizedRms)
            }
            onCaptureCompleted = { metadata, pcmFile ->
                Log.i(TAG, "Audio capture finished. Bytes: ${metadata.totalBytes}, Duration: ${metadata.durationMs} ms")
                _lastCapturedMetadata.value = metadata
                _assistantState.value = AssistantState.Captured(metadata)
                updateNotification(isRecording = false)

                // ── Gateway upload ──────────────────────────────────────────
                val clientMsgId = UUID.randomUUID().toString()
                serviceScope.launch(Dispatchers.IO) {
                    try {
                        if (pcmFile == null || !pcmFile.exists()) {
                            Log.e(TAG, "PCM file missing – skipping gateway upload")
                            resumeWakeWordStandbyAfterCapture()
                            return@launch
                        }
                        // Show processing state while network call is in flight
                        _assistantState.value = AssistantState.Processing

                        val wavFile = File(cacheDir, "last_utterance.wav")
                        pcmToWav(pcmFile, wavFile)

                        Log.i(TAG, "Sending to gateway [session=$sessionId, clientMsgId=$clientMsgId, wavBytes=${wavFile.length()}]")
                        val currentMerchantToken = merchantBearerToken
                            ?: throw java.io.IOException("Merchant session missing. Please log in again.")
                        val result = voiceGatewayClient.sendTurn(
                            audioFile    = wavFile,
                            sessionId    = sessionId,
                            merchantToken = currentMerchantToken,
                            clientMsgId  = clientMsgId,
                            language     = "hi-IN,en-IN"
                        )
                        Log.i(TAG,
                            "Gateway OK | status=${result.status} | transcript=\"${result.transcript}\" " +
                            "| replyText=\"${result.replyText}\" | kind=${result.replyKind} " +
                            "| dialogueState=${result.dialogueState} | ledgerCommitted=${result.ledgerCommitted} " +
                            "| duplicate=${result.duplicate}"
                        )
                        if (result.ledgerCommitted) {
                            Log.i(TAG, "LEDGER COMMITTED – transaction saved (txId=${result.ledgerTransactionId})")
                        }
                        val awaitingValue = result.awaiting?.trim()?.lowercase().orEmpty()
                        val stateValue = result.dialogueState?.trim()?.lowercase().orEmpty()

                        val confirmationPending =
                            awaitingValue.contains("confirm") ||
                                    awaitingValue == "yes_no" ||
                                    stateValue.contains("awaiting_confirmation") ||
                                    stateValue.contains("awaiting confirmation")

                        isConversationActive.set(confirmationPending)

                        Log.i(
                            TAG,
                            "Conversation status: pending=$confirmationPending, " +
                                    "awaiting=${result.awaiting}, state=${result.dialogueState}"
                        )
                        // Transition to Responding state so the UI can display reply
                        _assistantState.value = AssistantState.Responding(
                            transcript      = result.transcript,
                            replyText       = result.replyText,
                            replyKind       = result.replyKind,
                            dialogueState   = result.dialogueState,
                            ledgerCommitted = result.ledgerCommitted,
                            duplicate       = result.duplicate
                        )
                        // Do not replay duplicate replies. Keep wake-word paused while audio plays.
                        if (!result.duplicate && !result.audioBase64.isNullOrBlank()) {
                            try {
                                playGatewayAudio(result.audioBase64)
                            } catch (playbackError: Exception) {
                                Log.e(TAG, "Could not play gateway audio", playbackError)
                            }
                        } else {
                            Log.i(TAG, "No playable gateway audio; status=${result.audioStatus}, duplicate=${result.duplicate}")
                        }
                    } catch (e: com.vaada.network.VoiceGatewayException) {
                        Log.e(TAG, "Gateway error [${e.errorCode}]: ${e.message}")
                        _assistantState.value = AssistantState.Error("Gateway: ${e.message}")
                    } catch (e: java.io.IOException) {
                        Log.e(TAG, "Network IO error contacting gateway", e)
                        _assistantState.value = AssistantState.Error("Network error – check connection")
                    } catch (e: Exception) {
                        Log.e(TAG, "Unexpected error during gateway upload", e)
                        _assistantState.value = AssistantState.Error("Unexpected error: ${e.message}")
                    } finally {
                        if (isConversationActive.get() && isServiceRunning.get()) {
                            Log.i(TAG, "Confirmation pending. Listening again without wake word...")

                            serviceScope.launch(Dispatchers.Main) {
                                delay(400)

                                if (isConversationActive.get() && isServiceRunning.get()) {
                                    _assistantState.value = AssistantState.Listening(0f)
                                    updateNotification(isRecording = true)

                                    try {
                                        audioCaptureManager.startCapture(serviceScope)
                                    } catch (e: Exception) {
                                        Log.e(TAG, "Failed to continue confirmation listening", e)
                                        isConversationActive.set(false)
                                        resumeWakeWordStandbyAfterCapture()
                                    }
                                } else {
                                    resumeWakeWordStandbyAfterCapture()
                                }
                            }
                        } else {
                            resumeWakeWordStandbyAfterCapture()
                        }
                    }
                }
            }
            onError = { errorMessage ->
                Log.e(TAG, "AudioCaptureManager error: $errorMessage")
                _assistantState.value = AssistantState.Error(errorMessage)
                updateNotification(isRecording = false)
                resumeWakeWordStandbyAfterCapture()
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val action = intent?.action ?: ACTION_START_SERVICE
        Log.i(TAG, "onStartCommand received action: $action")

        when (action) {
            ACTION_STOP_SERVICE -> {
                handleStopService()
                return START_NOT_STICKY
            }

            ACTION_START_SERVICE -> {
                handleStartService()
            }

            ACTION_START_RECORDING -> {
                handleManualStartRecording()
            }

            ACTION_STOP_RECORDING -> {
                handleManualStopRecording()
            }
        }

        return START_STICKY
    }

    /** Decode and play the WAV returned by the voice gateway. This suspends until playback ends. */
    private suspend fun playGatewayAudio(encodedAudio: String) {
        val audioFile = withContext(Dispatchers.IO) {
            val bytes = Base64.decode(encodedAudio, Base64.DEFAULT)
            require(bytes.isNotEmpty()) { "Gateway returned empty audio" }
            File(cacheDir, "gateway_reply.wav").apply { writeBytes(bytes) }
        }

        Log.i(TAG, "Playing Gnani audio: ${audioFile.length()} bytes")

        withContext(Dispatchers.Main) {
            suspendCancellableCoroutine<Unit> { continuation ->
                val player = MediaPlayer()
                var released = false

                fun releasePlayer() {
                    if (!released) {
                        released = true
                        try { player.release() } catch (_: Exception) { }
                    }
                }

                continuation.invokeOnCancellation { releasePlayer() }

                try {
                    player.setDataSource(audioFile.absolutePath)
                    player.setOnCompletionListener {
                        Log.i(TAG, "Gnani audio playback completed")
                        releasePlayer()
                        if (continuation.isActive) continuation.resume(Unit)
                    }
                    player.setOnErrorListener { _, what, extra ->
                        Log.e(TAG, "Gnani audio playback error: what=$what extra=$extra")
                        releasePlayer()
                        if (continuation.isActive) {
                            continuation.resumeWithException(
                                IllegalStateException("Gateway audio playback failed ($what/$extra)")
                            )
                        }
                        true
                    }
                    player.setOnPreparedListener {
                        Log.i(TAG, "Gnani audio playback started")
                        it.start()
                    }
                    player.prepareAsync()
                } catch (e: Exception) {
                    releasePlayer()
                    if (continuation.isActive) continuation.resumeWithException(e)
                }
            }
        }
    }

    // ── PCM → WAV conversion ───────────────────────────────────────────────────
    private fun pcmToWav(pcmFile: File, wavFile: File) {
        val pcmBytes = pcmFile.readBytes()
        val sampleRate = 16000
        val channels: Short = 1
        val bitsPerSample: Short = 16
        val byteRate = sampleRate * channels * (bitsPerSample / 8)
        val blockAlign = (channels * (bitsPerSample / 8)).toShort()
        val dataLen = pcmBytes.size
        val chunkSize = 36 + dataLen
        fun Int.le4(): ByteArray = byteArrayOf(
            (this and 0xFF).toByte(), ((this shr 8) and 0xFF).toByte(),
            ((this shr 16) and 0xFF).toByte(), ((this shr 24) and 0xFF).toByte()
        )
        fun Short.le2(): ByteArray = byteArrayOf(
            (this.toInt() and 0xFF).toByte(), ((this.toInt() shr 8) and 0xFF).toByte()
        )
        FileOutputStream(wavFile).use { out ->
            out.write("RIFF".toByteArray(Charsets.US_ASCII))
            out.write(chunkSize.le4())
            out.write("WAVE".toByteArray(Charsets.US_ASCII))
            out.write("fmt ".toByteArray(Charsets.US_ASCII))
            out.write(16.le4())                      // Subchunk1Size = 16 for PCM
            out.write((1).toShort().le2())            // AudioFormat = 1 (PCM)
            out.write(channels.le2())
            out.write(sampleRate.le4())
            out.write(byteRate.le4())
            out.write(blockAlign.le2())
            out.write(bitsPerSample.le2())
            out.write("data".toByteArray(Charsets.US_ASCII))
            out.write(dataLen.le4())
            out.write(pcmBytes)
        }
        Log.d(TAG, "PCM→WAV: ${pcmFile.length()} PCM bytes → ${wavFile.length()} WAV bytes")
    }
    // ──────────────────────────────────────────────────────────────────────────

    /**
     * Verify speaker voice against the enrolled voiceprint.
     * Called from MainActivity after capturing a voice sample for auth (not for voice turns).
     *
     * This is a coroutine-compatible call: it must be launched from a coroutine scope.
     * Updates [voiceAuthSession] state directly on the companion StateFlow.
     *
     * Microphone ownership: caller must ensure wake-word service is NOT capturing audio
     * before calling this. The [wavFile] is a WAV already converted from PCM.
     *
     * @param accountId  The merchant's account id (ledger customer id).
     * @param sessionId  The session id that will be used for voice turns too.
     * @param wavFile    A valid WAV (PCM16 mono 16 kHz, 1.5–30 s).
     */
    suspend fun verifyVoice(accountId: String, sessionId: String, wavFile: File) {
        Log.i(TAG, "verifyVoice: accountId=$accountId sessionId=$sessionId wavBytes=${wavFile.length()}")
        setVoiceAuthSession(VoiceAuthSession.Verifying)
        try {
            val result = voiceAuthClient.verify(accountId, sessionId, wavFile)
            Log.i(TAG, "verifyVoice result: status=${result.status} reason=${result.reason}")
            val newSession = when (result.status) {
                "verified" -> {
                    val token = result.authToken ?: run {
                        Log.e(TAG, "verified but no auth_token in response — treating as network error")
                        VoiceAuthSession.NetworkError("Server returned verified but no auth_token")
                    }
                    if (token is String) {
                        val expiresAt = System.currentTimeMillis() + ((result.expiresInS ?: 300) * 1000L)
                        VoiceAuthSession.Verified(accountId = accountId, sessionId = sessionId, token = token, expiresAtMs = expiresAt)
                    } else token as VoiceAuthSession
                }
                "inconclusive" -> VoiceAuthSession.Inconclusive(result.reason)
                "rejected" -> {
                    if (result.reason == "locked") {
                        val unlockAt = System.currentTimeMillis() + ((result.retryAfterS ?: 300) * 1000L)
                        VoiceAuthSession.Locked(retryAfterMs = unlockAt)
                    } else if (result.reason == "not_enrolled") {
                        VoiceAuthSession.NotEnrolled(accountId = accountId)
                    } else {
                        VoiceAuthSession.Rejected(reason = result.reason)
                    }
                }
                "unavailable" -> VoiceAuthSession.Unavailable(result.reason)
                else -> VoiceAuthSession.NetworkError("Unknown status: ${result.status}")
            }
            setVoiceAuthSession(newSession)
        } catch (e: java.io.IOException) {
            Log.e(TAG, "verifyVoice network error", e)
            setVoiceAuthSession(VoiceAuthSession.NetworkError(e.message ?: "Network error"))
        } catch (e: Exception) {
            Log.e(TAG, "verifyVoice unexpected error", e)
            setVoiceAuthSession(VoiceAuthSession.NetworkError(e.message ?: "Unexpected error"))
        }
    }

    /**
     * Server-side logout: invalidate the current auth token.
     * Non-fatal. Must be called on user logout and account switch.
     */
    suspend fun logoutVoiceAuth(token: String) {
        try {
            voiceAuthClient.logout(token)
            Log.i(TAG, "logoutVoiceAuth: server-side session invalidated")
        } catch (e: Exception) {
            Log.w(TAG, "logoutVoiceAuth: non-fatal error", e)
        } finally {
            setVoiceAuthSession(VoiceAuthSession.None)
        }
    }
    // ──────────────────────────────────────────────────────────────────────────

    private fun handleStartService() {
        promoteToForeground(isRecording = false)
        acquireWakeLock()
        isServiceRunning.set(true)
        isHandoffInProgress.set(false)
        isManualRecordingActive.set(false)
        _assistantState.value = AssistantState.Standby

        initAndStartWakeWordDetector()
    }

    private fun initAndStartWakeWordDetector() {
        if (!isServiceRunning.get()) return

        stopWakeWordDetectorOnly()

        wakeWordJob = serviceScope.launch(Dispatchers.Default) {
            try {
                Log.i(TAG, "Initializing WakeWordDetector (hey_jarvis_v0.1.onnx, threshold=${_wakeWordThreshold.value})...")
                val detector = WakeWordDetector(
                    context = applicationContext,
                    modelFile = "hey_jarvis_v0.1.onnx",
                    verifierFile = null,
                    minScore = _wakeWordThreshold.value,
                    minVerifierScore = 0.0f,
                    onDetected = { score ->
                        onWakeWordTriggered(score)
                    }
                ).apply {
                    onScoreEvaluated = { score, threshold, triggered ->
                        _lastEvaluatedScore.value = score
                        if (score > _peakScore.value) {
                            _peakScore.value = score
                            peakDecayJob?.cancel()
                            peakDecayJob = serviceScope.launch {
                                delay(3000L) // 3-second peak hold
                                _peakScore.value = 0f
                            }
                        }
                    }
                }
                wakeWordDetector = detector
                Log.i(TAG, "WakeWordDetector initialized. Starting detection loop in standby...")
                detector.startDetection()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to initialize or run WakeWordDetector", e)
                serviceScope.launch(Dispatchers.Main) {
                    _assistantState.value = AssistantState.Error("Wake-Word Init Failed: ${e.message}")
                }
            }
        }
    }

    /**
     * Confirmed wake-word callback:
     * 1. Pauses wake-word detection & releases its microphone capture (T0 -> T1).
     * 2. Brief hardware transition buffer (T1 -> T2).
     * 3. Promotes state to LISTENING.
     * 4. Starts AudioCaptureManager to record merchant's spoken command (T2 -> T3 -> T4).
     */
    private fun onWakeWordTriggered(score: Float) {
        if (!isServiceRunning.get()) return
        if (!isHandoffInProgress.compareAndSet(false, true)) {
            Log.w(TAG, "Wake word trigger ignored: handoff already in progress")
            return
        }

        val t0WakeDetected = System.currentTimeMillis()
        Log.i(TAG, "Wake word 'Hey Jarvis' confirmed! Score: $score (T0=$t0WakeDetected). Pausing detector for mic handoff...")
        _lastWakeWordScore.value = score
        _wakeWordDetectionCount.value += 1

        serviceScope.launch(Dispatchers.Main) {
            try {
                // Step 1: Pause detector and stop its AudioRecord (T0 -> T1)
                val pauseDurationMs = wakeWordDetector?.pauseDetection() ?: 0L
                val t1DetectorPaused = System.currentTimeMillis()
                Log.d("HandoffProfiler", "T0->T1: Detector paused in ${t1DetectorPaused - t0WakeDetected}ms (audioRecord.stop() = ${pauseDurationMs}ms)")

                // Step 2: Hardware safety buffer (T1 -> T2)
                if (HANDOFF_SAFETY_DELAY_MS > 0) {
                    delay(HANDOFF_SAFETY_DELAY_MS)
                }
                val t2CaptureStart = System.currentTimeMillis()
                Log.d("HandoffProfiler", "T1->T2: Buffer delay elapsed in ${t2CaptureStart - t1DetectorPaused}ms (target: ${HANDOFF_SAFETY_DELAY_MS}ms)")

                // Step 3: Transition UI & Notification to LISTENING
                _assistantState.value = AssistantState.Listening(0f)
                updateNotification(isRecording = true)

                // Step 4: Start recording the merchant command (T2 -> T3 -> T4)
                Log.i(TAG, "Starting AudioCaptureManager for merchant command at T2=$t2CaptureStart...")
                audioCaptureManager.startCapture(serviceScope, handoffStartTimeMs = t0WakeDetected)
            } catch (e: Exception) {
                Log.e(TAG, "Exception during mic handoff to AudioCaptureManager", e)
                _assistantState.value = AssistantState.Error("Handoff Failed: ${e.message}")
                isHandoffInProgress.set(false)
                resumeWakeWordStandbyAfterCapture()
            }
        }
    }

    /**
     * Resumes wake-word standby detection after utterance capture finishes.
     */
    private fun resumeWakeWordStandbyAfterCapture() {
        if (!isServiceRunning.get() || isManualRecordingActive.get()) return

        serviceScope.launch(Dispatchers.Default) {
            // Give AudioRecord hardware 300ms to cleanly release
            delay(300)

            if (isServiceRunning.get() && !isManualRecordingActive.get()) {
                isHandoffInProgress.set(false)
                serviceScope.launch(Dispatchers.Main) {
                    _assistantState.value = AssistantState.Standby
                }
                try {
                    Log.i(TAG, "Resuming WakeWordDetector standby detection...")
                    wakeWordDetector?.startDetection()
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to resume wake-word detection", e)
                    serviceScope.launch(Dispatchers.Main) {
                        _assistantState.value = AssistantState.Error("Wake-Word Resume Failed: ${e.message}")
                    }
                }
            }
        }
    }

    /**
     * Manual Push-to-Talk activation:
     * Pauses wake-word detection and starts manual recording.
     */
    private fun handleManualStartRecording() {
        if (!isServiceRunning.get()) return

        isManualRecordingActive.set(true)
        isHandoffInProgress.set(true)

        serviceScope.launch(Dispatchers.Main) {
            try {
                wakeWordDetector?.pauseDetection()
                if (HANDOFF_SAFETY_DELAY_MS > 0) {
                    delay(HANDOFF_SAFETY_DELAY_MS)
                }
                promoteToForeground(isRecording = true)
                _assistantState.value = AssistantState.Listening(0f)
                audioCaptureManager.startCapture(serviceScope)
            } catch (e: Exception) {
                Log.e(TAG, "Failed manual start recording", e)
                _assistantState.value = AssistantState.Error(e.message ?: "Manual record error")
            }
        }
    }

    /**
     * Manual Push-to-Talk stop.
     */
    private fun handleManualStopRecording() {
        isManualRecordingActive.set(false)
        audioCaptureManager.stopCapture()
    }

    private fun promoteToForeground(isRecording: Boolean) {
        val notification = AudioNotification.buildNotification(this, isRecording)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                AudioNotification.NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(AudioNotification.NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(isRecording: Boolean) {
        try {
            val notification = AudioNotification.buildNotification(this, isRecording)
            NotificationManagerCompat.from(this).notify(AudioNotification.NOTIFICATION_ID, notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "Missing POST_NOTIFICATIONS permission when updating notification", e)
        }
    }

    private fun stopWakeWordDetectorOnly() {
        try {
            wakeWordJob?.cancel()
            wakeWordJob = null
            wakeWordDetector?.pauseDetection()
            wakeWordDetector?.releaseResources()
            wakeWordDetector = null
        } catch (e: Exception) {
            Log.w(TAG, "Error stopping wake-word detector", e)
        }
    }

    private fun handleStopService() {
        Log.i(TAG, "handleStopService: Shutting down assistant service")
        isServiceRunning.set(false)
        isHandoffInProgress.set(false)
        isManualRecordingActive.set(false)
        isConversationActive.set(false)

        audioCaptureManager.release()
        stopWakeWordDetectorOnly()
        releaseWakeLock()

        _assistantState.value = AssistantState.Stopped
        _peakScore.value = 0f
        peakDecayJob?.cancel()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        stopSelf()
    }

    private fun acquireWakeLock() {
        try {
            if (wakeLock?.isHeld == false) {
                wakeLock?.acquire() // 10 min timeout
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to acquire WakeLock", e)
        }
    }

    private fun releaseWakeLock() {
        try {
            if (wakeLock?.isHeld == true) {
                wakeLock?.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Failed to release WakeLock", e)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        Log.i(TAG, "VaadaAudioService onDestroy")
        handleStopService()
        serviceScope.cancel()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
