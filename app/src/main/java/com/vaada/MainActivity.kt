package com.vaada

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.vaada.models.AssistantState
import com.vaada.models.AudioCaptureMetadata
import com.vaada.service.VaadaAudioService

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    Scaffold { innerPadding ->
                        VaadaAssistantScreen(modifier = Modifier.padding(innerPadding))
                    }
                }
            }
        }
    }
}

@Composable
fun VaadaAssistantScreen(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val assistantState by VaadaAudioService.assistantState.collectAsState()
    val lastMetadata by VaadaAudioService.lastCapturedMetadata.collectAsState()
    val detectionCount by VaadaAudioService.wakeWordDetectionCount.collectAsState()
    val lastScore by VaadaAudioService.lastWakeWordScore.collectAsState()
    val threshold by VaadaAudioService.wakeWordThreshold.collectAsState()
    val lastEvaluatedScore by VaadaAudioService.lastEvaluatedScore.collectAsState()

    // Permissions check
    val requiredPermissions = remember {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            arrayOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS)
        } else {
            arrayOf(Manifest.permission.RECORD_AUDIO)
        }
    }

    var hasPermissions by remember {
        mutableStateOf(
            requiredPermissions.all {
                ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
            }
        )
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        hasPermissions = results.values.all { it }
    }

    LaunchedEffect(Unit) {
        if (!hasPermissions) {
            permissionLauncher.launch(requiredPermissions)
        }
    }

    val scrollState = rememberScrollState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(20.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // App Header
        AppHeader()

        Spacer(modifier = Modifier.height(16.dp))

        // Permission Banner
        if (!hasPermissions) {
            PermissionCard {
                permissionLauncher.launch(requiredPermissions)
            }
            Spacer(modifier = Modifier.height(16.dp))
        }

        // Assistant Status & Wake-Word Monitor Card
        AssistantStatusCard(
            state = assistantState,
            hasPermissions = hasPermissions,
            detectionCount = detectionCount,
            lastTriggerScore = lastScore,
            threshold = threshold,
            lastEvaluatedScore = lastEvaluatedScore,
            onStartService = { VaadaAudioService.startService(context) },
            onStopService = { VaadaAudioService.stopService(context) },
            onStopRecording = { VaadaAudioService.stopRecording(context) }
        )

        Spacer(modifier = Modifier.height(16.dp))

        // Push-to-Talk Fallback Control
        PushToTalkCard(
            state = assistantState,
            isEnabled = hasPermissions && assistantState !is AssistantState.Stopped,
            onStartRecording = { VaadaAudioService.startRecording(context) },
            onStopRecording = { VaadaAudioService.stopRecording(context) }
        )

        Spacer(modifier = Modifier.height(16.dp))

        // Audio Capture Inspector (Shows captured utterance stats)
        AudioInspectorCard(metadata = lastMetadata)
    }
}

@Composable
private fun AppHeader() {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.Start
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                modifier = Modifier
                    .size(10.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF2E7D32))
            )
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = "VAADA KHATA",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.2.sp,
                color = MaterialTheme.colorScheme.primary
            )
        }
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = "Voice Assistant & Ledger",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold
        )
        Text(
            text = "Target: CMF Phone 2 Pro | Branch: feature/android-wakeword",
            fontSize = 11.sp,
            color = MaterialTheme.colorScheme.outline
        )
    }
}

@Composable
private fun PermissionCard(onRequestPermissions: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "Permissions Required",
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onErrorContainer
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Microphone and Notification permissions are needed to run the foreground audio service.",
                fontSize = 13.sp,
                color = MaterialTheme.colorScheme.onErrorContainer
            )
            Spacer(modifier = Modifier.height(12.dp))
            Button(
                onClick = onRequestPermissions,
                colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error)
            ) {
                Text("Grant Permissions", color = MaterialTheme.colorScheme.onError)
            }
        }
    }
}

@Composable
private fun AssistantStatusCard(
    state: AssistantState,
    hasPermissions: Boolean,
    detectionCount: Int,
    lastTriggerScore: Float,
    threshold: Float,
    lastEvaluatedScore: Float,
    onStartService: () -> Unit,
    onStopService: () -> Unit,
    onStopRecording: () -> Unit
) {
    val statusColor by animateColorAsState(
        targetValue = when (state) {
            is AssistantState.Stopped -> Color(0xFF757575)
            is AssistantState.Standby -> Color(0xFF1976D2)
            is AssistantState.Listening -> Color(0xFF2E7D32)
            is AssistantState.Captured -> Color(0xFF7B1FA2)
            is AssistantState.Error -> Color(0xFFC62828)
        },
        label = "statusColor"
    )

    val statusBadgeText = when (state) {
        is AssistantState.Stopped -> "OFFLINE"
        is AssistantState.Standby -> "STANDBY"
        is AssistantState.Listening -> "LISTENING"
        is AssistantState.Captured -> "CAPTURED"
        is AssistantState.Error -> "ERROR"
    }

    val statusDescription = when (state) {
        is AssistantState.Stopped -> "Service Inactive. Tap Start to activate local wake-word listener."
        is AssistantState.Standby -> "Listening for wake word \"Hey Jarvis\" (or tap Push-to-Talk)."
        is AssistantState.Listening -> "Recording merchant command... (Speak now; auto-stops on silence)."
        is AssistantState.Captured -> "Command recorded & verified! Resuming standby listener..."
        is AssistantState.Error -> "Error: ${state.message}"
    }

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "VOICE ENGINE & FOREGROUND SERVICE",
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.outline
                    )
                    Text(
                        text = "Wake Phrase: \"Hey Jarvis\"",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.primary
                    )
                }
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(statusColor.copy(alpha = 0.15f))
                        .padding(horizontal = 8.dp, vertical = 4.dp)
                ) {
                    Text(
                        text = statusBadgeText,
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = statusColor
                    )
                }
            }

            Spacer(modifier = Modifier.height(10.dp))

            Text(
                text = statusDescription,
                fontSize = 13.sp,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Live RMS Audio Level Bar when Listening
            if (state is AssistantState.Listening) {
                Spacer(modifier = Modifier.height(12.dp))
                val animatedRms by animateFloatAsState(
                    targetValue = state.amplitudeRms,
                    label = "rmsProgress"
                )
                Text(
                    text = "Live Mic Level (RMS):",
                    fontSize = 11.sp,
                    color = MaterialTheme.colorScheme.outline
                )
                Spacer(modifier = Modifier.height(4.dp))
                LinearProgressIndicator(
                    progress = { animatedRms.coerceIn(0f, 1f) },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(8.dp)
                        .clip(RoundedCornerShape(4.dp)),
                    color = Color(0xFF2E7D32)
                )
            }

            Spacer(modifier = Modifier.height(12.dp))

            // Wake-Word Telemetry
            InspectorRow(label = "Detection Threshold", value = String.format("%.2f", threshold))
            InspectorRow(
                label = "Live / Last Score",
                value = if (lastEvaluatedScore > 0f) String.format("%.4f", lastEvaluatedScore) else "0.0000"
            )
            InspectorRow(
                label = "Last Trigger Score",
                value = if (lastTriggerScore > 0f) String.format("%.4f", lastTriggerScore) else "None"
            )
            InspectorRow(label = "Wake Word Triggers", value = "$detectionCount")

            Spacer(modifier = Modifier.height(16.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                if (state is AssistantState.Stopped) {
                    Button(
                        onClick = onStartService,
                        enabled = hasPermissions,
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Text("Start Assistant Service")
                    }
                } else if (state is AssistantState.Listening) {
                    Button(
                        onClick = onStopRecording,
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFC62828))
                    ) {
                        Text("⏹  Finish Utterance Now")
                    }
                } else {
                    OutlinedButton(
                        onClick = onStopService,
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(10.dp)
                    ) {
                        Text("Stop Assistant (Explicit)")
                    }
                }
            }
        }
    }
}

@Composable
private fun PushToTalkCard(
    state: AssistantState,
    isEnabled: Boolean,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit
) {
    val isRecording = state is AssistantState.Listening

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Text(
                    text = "MANUAL PUSH-TO-TALK FALLBACK",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.outline
                )
                Text(
                    text = if (isEnabled) "Ready" else "Service Inactive",
                    fontSize = 11.sp,
                    color = if (isEnabled) MaterialTheme.colorScheme.primary else Color.Gray
                )
            }

            Spacer(modifier = Modifier.height(12.dp))

            Button(
                onClick = {
                    if (isRecording) {
                        onStopRecording()
                    } else {
                        onStartRecording()
                    }
                },
                enabled = isEnabled,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
                shape = RoundedCornerShape(12.dp),
                colors = ButtonDefaults.buttonColors(
                    containerColor = if (isRecording) Color(0xFFC62828) else MaterialTheme.colorScheme.primary
                )
            ) {
                Text(
                    text = if (isRecording) "⏹  Stop Recording Command" else "🎙  Push to Talk (Manual Trigger)",
                    fontSize = 14.sp,
                    fontWeight = FontWeight.Bold
                )
            }

            Spacer(modifier = Modifier.height(6.dp))
            Text(
                text = "Alternative to saying \"Hey Jarvis\". Tap to record manual utterance.",
                fontSize = 11.sp,
                color = MaterialTheme.colorScheme.outline
            )
        }
    }
}

@Composable
private fun AudioInspectorCard(metadata: AudioCaptureMetadata?) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                text = "CAPTURED UTTERANCE INSPECTOR",
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.outline
            )
            Spacer(modifier = Modifier.height(8.dp))

            if (metadata == null) {
                Text(
                    text = "No command recorded yet. Start the assistant and say \"Hey Jarvis\" or tap Push-to-Talk.",
                    fontSize = 13.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                InspectorRow(label = "Format", value = "${metadata.sampleRate} Hz, Mono (${metadata.audioFormat})")
                InspectorRow(label = "Duration", value = "${metadata.durationMs} ms (${String.format("%.2f", metadata.durationMs / 1000f)} sec)")
                InspectorRow(label = "Total Size", value = "${metadata.totalBytes} bytes (${metadata.totalBytes / 1024} KB)")
                InspectorRow(label = "Max Amplitude", value = "${metadata.maxAmplitude} / 32767")
                InspectorRow(label = "Average RMS", value = String.format("%.4f", metadata.averageRms))
                InspectorRow(label = "Storage Path", value = "cache/last_utterance.pcm")
            }
        }
    }
}

@Composable
private fun InspectorRow(label: String, value: String) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label,
            fontSize = 12.sp,
            color = MaterialTheme.colorScheme.outline
        )
        Text(
            text = value,
            fontSize = 12.sp,
            fontWeight = FontWeight.Medium,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurface
        )
    }
}
