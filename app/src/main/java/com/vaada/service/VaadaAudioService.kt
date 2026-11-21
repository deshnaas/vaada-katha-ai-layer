package com.vaada.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.suxsem.androidwakeword.WakeWordDetector
import com.vaada.audio.AudioCaptureManager
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

    // Concurrency & Handoff guards
    private val isServiceRunning = AtomicBoolean(false)
    private val isHandoffInProgress = AtomicBoolean(false)
    private val isManualRecordingActive = AtomicBoolean(false)

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
            onCaptureCompleted = { metadata, _ ->
                Log.i(TAG, "Audio capture finished. Bytes: ${metadata.totalBytes}, Duration: ${metadata.durationMs} ms")
                _lastCapturedMetadata.value = metadata
                _assistantState.value = AssistantState.Captured(metadata)
                updateNotification(isRecording = false)

                // Resume Wake-Word Standby Listener after recording completes
                resumeWakeWordStandbyAfterCapture()
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
                wakeLock?.acquire(600000L) // 10 min timeout
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
