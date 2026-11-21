package com.vaada.ui.assistant

import androidx.compose.animation.*
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.models.AssistantState
import com.vaada.models.AudioCaptureMetadata
import com.vaada.ui.theme.*

@Composable
fun HomeScreen(
    state: AssistantState,
    hasPermissions: Boolean,
    lastMetadata: AudioCaptureMetadata?,
    shopName: String,
    onStartService: () -> Unit,
    onStopService: () -> Unit,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit,
    onRequestPermissions: () -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()
    val isListening = state is AssistantState.Listening
    val isStopped = state is AssistantState.Stopped
    val isStandby = state is AssistantState.Standby
    val isProcessing = state is AssistantState.Processing

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(KiranaCreamBg)
            .verticalScroll(scrollState)
            .padding(horizontal = 20.dp, vertical = 16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            // ── Top bar ───────────────────────────────────────────────────────
            MerchantTopBar(state = state, shopName = shopName)

            Spacer(modifier = Modifier.height(20.dp))

            if (!hasPermissions) {
                PermissionNoticeCard(onRequestPermissions = onRequestPermissions)
                Spacer(modifier = Modifier.height(16.dp))
            }

            // ── Headline / status text ────────────────────────────────────────
            Text(
                text = when (state) {
                    is AssistantState.Processing -> "समझ रहा हूँ..."
                    is AssistantState.Responding -> "जवाब तैयार है"
                    else -> "नमस्ते! मैं क्या लिखूँ?"
                },
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary,
                textAlign = TextAlign.Center
            )
            Spacer(modifier = Modifier.height(6.dp))

            val statusSubtitle = when (state) {
                is AssistantState.Stopped   -> "सहायक बंद है • नीचे दिए बटन से शुरू करें"
                is AssistantState.Standby   -> "\"Hey Jarvis\" बोलें या बटन दबाकर कहें"
                is AssistantState.Listening -> "सुन रहा हूँ... अपनी बात बोलें"
                is AssistantState.Captured  -> "रिकॉर्ड हो गया • gateway पर भेज रहा है..."
                is AssistantState.Processing -> "Gateway से जवाब मिल रहा है..."
                is AssistantState.Responding -> state.replyText.ifBlank { "जवाब तैयार है" }
                is AssistantState.Error     -> "त्रुटि: ${state.message}"
            }

            Text(
                text = statusSubtitle,
                style = MaterialTheme.typography.bodyMedium,
                color = when (state) {
                    is AssistantState.Error     -> KiranaBaakiRed
                    is AssistantState.Responding -> KiranaGreenPrimary
                    else                        -> KiranaTextSecondary
                },
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 8.dp)
            )

            Spacer(modifier = Modifier.height(28.dp))

            // ── Voice Orb ─────────────────────────────────────────────────────
            VoiceOrb(
                state = state,
                onClick = {
                    when {
                        !hasPermissions -> onRequestPermissions()
                        isStopped       -> onStartService()
                        isListening     -> onStopRecording()
                        else            -> onStartRecording()
                    }
                }
            )

            Spacer(modifier = Modifier.height(28.dp))

            // ── Gateway reply card ────────────────────────────────────────────
            GatewayReplyCard(state = state, metadata = lastMetadata)
        }

        // ── Push-to-talk button ───────────────────────────────────────────────
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 24.dp, bottom = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            val buttonColor by animateColorAsState(
                targetValue = when {
                    isListening     -> Color(0xFFC62828)
                    isStopped       -> KiranaGreenLight
                    isProcessing    -> KiranaTextMuted
                    else            -> KiranaGreenPrimary
                },
                label = "pttButtonColor"
            )

            Button(
                onClick = {
                    when {
                        !hasPermissions -> onRequestPermissions()
                        isListening     -> onStopRecording()
                        isStopped       -> onStartService()
                        isProcessing    -> { /* no-op while processing */ }
                        else            -> onStartRecording()
                    }
                },
                modifier = Modifier.fillMaxWidth().height(58.dp),
                shape = RoundedCornerShape(29.dp),
                colors = ButtonDefaults.buttonColors(containerColor = buttonColor),
                elevation = ButtonDefaults.buttonElevation(defaultElevation = 3.dp, pressedElevation = 1.dp)
            ) {
                if (isProcessing) {
                    CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
                    Spacer(modifier = Modifier.width(10.dp))
                    Text("प्रोसेसिंग...", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
                } else {
                    Icon(
                        imageVector = when {
                            isListening -> KiranaIcons.Mic
                            isStopped   -> Icons.Default.PlayArrow
                            else        -> KiranaIcons.Mic
                        },
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(24.dp)
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Text(
                        text = when {
                            isListening -> "⏹  बोलना समाप्त करें (Done)"
                            isStopped   -> "सहायक चालू करें (Start)"
                            else        -> "🎙  दबाकर बोलें (Push to Talk)"
                        },
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.White
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))
            Text(
                text = if (isStandby) "हाथ खाली नहीं हैं? बस \"Hey Jarvis\" बोलें" else "किराना वॉइस खाता",
                fontSize = 12.sp,
                color = KiranaTextMuted
            )
        }
    }
}

// ── Top bar ───────────────────────────────────────────────────────────────────

@Composable
private fun MerchantTopBar(state: AssistantState, shopName: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier.size(9.dp).clip(CircleShape).background(KiranaGreenPrimary)
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text("VAADA KHATA", fontSize = 11.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.4.sp, color = KiranaGreenPrimary)
            }
            Text(
                text = shopName.ifBlank { "आपकी दुकान" },
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary
            )
        }

        val dotColor = when (state) {
            is AssistantState.Listening   -> KiranaJamaGreen
            is AssistantState.Processing  -> Color(0xFFFFA000)
            is AssistantState.Responding  -> KiranaGreenPrimary
            is AssistantState.Standby     -> KiranaGreenPrimary
            is AssistantState.Captured    -> KiranaGreenLight
            is AssistantState.Error       -> KiranaBaakiRed
            else                          -> KiranaTextMuted
        }
        val statusLabel = when (state) {
            is AssistantState.Listening   -> "सुन रहा है"
            is AssistantState.Processing  -> "प्रोसेसिंग"
            is AssistantState.Responding  -> "जवाब"
            is AssistantState.Standby     -> "Active"
            is AssistantState.Captured    -> "भेज रहा है"
            is AssistantState.Error       -> "त्रुटि"
            else                          -> "बंद"
        }
        val isActive = state !is AssistantState.Stopped

        Surface(
            shape = RoundedCornerShape(20.dp),
            color = if (isActive) KiranaMintPill else KiranaSurface,
            border = androidx.compose.foundation.BorderStroke(1.dp, if (isActive) KiranaMint else KiranaBorder)
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(modifier = Modifier.size(7.dp).clip(CircleShape).background(dotColor))
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = statusLabel,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = if (isActive) KiranaGreenDark else KiranaTextSecondary
                )
            }
        }
    }
}

// ── Gateway reply card ────────────────────────────────────────────────────────

@Composable
private fun GatewayReplyCard(state: AssistantState, metadata: AudioCaptureMetadata?) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaSurface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBorder)
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Text(
                text = "नवीनतम जवाब (LATEST REPLY)",
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.8.sp,
                color = KiranaTextMuted
            )
            Spacer(modifier = Modifier.height(12.dp))

            when (state) {
                is AssistantState.Processing -> {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), color = KiranaGreenPrimary, strokeWidth = 2.dp)
                        Spacer(modifier = Modifier.width(10.dp))
                        Text("Gateway से जवाब मिल रहा है...", style = MaterialTheme.typography.bodyMedium, color = KiranaTextSecondary)
                    }
                }

                is AssistantState.Responding -> {
                    // Transcript
                    if (state.transcript.isNotBlank()) {
                        Text("आपने कहा:", fontSize = 12.sp, fontWeight = FontWeight.Medium, color = KiranaTextSecondary)
                        Spacer(modifier = Modifier.height(2.dp))
                        Text("\"${state.transcript}\"", fontSize = 15.sp, fontWeight = FontWeight.SemiBold, color = KiranaTextPrimary)
                        Spacer(modifier = Modifier.height(12.dp))
                    }

                    // Reply
                    if (state.replyText.isNotBlank()) {
                        val replyBg = when {
                            state.ledgerCommitted                           -> KiranaJamaGreenLight
                            state.replyKind.contains("CONFIRM", ignoreCase = true) -> KiranaMintPill
                            state.replyKind.contains("UNCLEAR", ignoreCase = true) -> KiranaBaakiRedLight
                            else                                            -> KiranaMintPill
                        }
                        val replyColor = when {
                            state.ledgerCommitted -> KiranaJamaGreen
                            state.replyKind.contains("UNCLEAR", ignoreCase = true) -> KiranaBaakiRed
                            else -> KiranaGreenDark
                        }
                        Surface(shape = RoundedCornerShape(12.dp), color = replyBg, border = androidx.compose.foundation.BorderStroke(1.dp, replyBg)) {
                            Column(modifier = Modifier.padding(12.dp)) {
                                if (state.ledgerCommitted) {
                                    Text("✓ Transaction committed", fontSize = 11.sp, fontWeight = FontWeight.Bold, color = KiranaJamaGreen)
                                    Spacer(modifier = Modifier.height(4.dp))
                                }
                                Text(state.replyText, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, color = replyColor)
                                if (!state.dialogueState.isNullOrBlank()) {
                                    Spacer(modifier = Modifier.height(6.dp))
                                    Text("State: ${state.dialogueState}", fontSize = 11.sp, color = KiranaTextMuted)
                                }
                            }
                        }
                    }

                    if (state.duplicate) {
                        Spacer(modifier = Modifier.height(6.dp))
                        Text("(duplicate response — not spoken again)", fontSize = 11.sp, color = KiranaTextMuted)
                    }
                }

                else -> {
                    // Idle suggestion chips — generic voice command examples (no real names)
                    Text("ऐसे बोलकर देखें:", fontSize = 13.sp, fontWeight = FontWeight.Medium, color = KiranaTextSecondary)
                    Spacer(modifier = Modifier.height(8.dp))
                    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        SuggestionChip(text = "\"ग्राहक का ₹200 उधार\"", modifier = Modifier.weight(1f))
                        SuggestionChip(text = "\"ग्राहक का जमा ₹500\"", modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun SuggestionChip(text: String, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = KiranaCreamBg,
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBorder)
    ) {
        Box(modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp), contentAlignment = Alignment.Center) {
            Text(text = text, fontSize = 12.sp, fontWeight = FontWeight.Medium, color = KiranaGreenPrimary)
        }
    }
}

@Composable
private fun PermissionNoticeCard(onRequestPermissions: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaBaakiRed.copy(alpha = 0.08f)),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBaakiRed.copy(alpha = 0.2f))
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text("माइक्रोफ़ोन अनुमति आवश्यक है", fontSize = 13.sp, fontWeight = FontWeight.Bold, color = KiranaBaakiRed)
                Text("आवाज़ से हिसाब लिखने के लिए माइक चालू करें", fontSize = 11.sp, color = KiranaTextSecondary)
            }
            Button(
                onClick = onRequestPermissions,
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = KiranaBaakiRed),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp)
            ) {
                Text("अनुमति दें", fontSize = 12.sp, color = Color.White)
            }
        }
    }
}
