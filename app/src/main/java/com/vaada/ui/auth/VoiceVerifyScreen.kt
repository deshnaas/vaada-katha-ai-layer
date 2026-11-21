package com.vaada.ui.auth

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.models.MerchantProfile
import com.vaada.models.VoiceAuthSession
import com.vaada.ui.theme.*

/**
 * Voice Verification Screen.
 *
 * Shown after account setup when the gateway is running in voice-auth mode (VAADA_VOICE_AUTH_MODE=enforce).
 * Uses POST /v1/auth/verify to authenticate the merchant's voice against their enrolled voiceprint.
 *
 * ENROLLMENT NOTE (from docs/voice_auth.md §4):
 *   "Enrollment is done by an operator through the admin endpoints, not by the app."
 *   POST /v1/admin/voiceprints/{account_id}/enroll requires X-Admin-Token.
 *   The Android app CANNOT perform enrollment. If not_enrolled, show contact instructions.
 *
 * AUDIO FORMAT (from docs/voice_auth.md §8):
 *   WAV PCM16, mono, 16 kHz, 1.5–30 s, ≥ ~1.2 s of active speech.
 *   Same format as /v1/voice/turn — reuses the existing pcmToWav converter.
 *
 * @param profile              Merchant profile from the account phase.
 * @param voiceAuthSession     Current voice auth session state (from ViewModel/MainActivity).
 * @param isRecording          Whether mic is currently active for voice capture.
 * @param amplitudeRms         Live amplitude from the microphone (0–1).
 * @param onStartRecording     Start mic capture for verification sample.
 * @param onStopRecording      Stop mic capture early (normally auto-stops on silence/max-duration).
 * @param onProceedAnyway      Navigate to dashboard without voice auth (only valid when status is Unavailable).
 * @param onLogout             Logout and return to Welcome screen.
 */
@Composable
fun VoiceVerifyScreen(
    profile: MerchantProfile,
    voiceAuthSession: VoiceAuthSession,
    isRecording: Boolean,
    amplitudeRms: Float,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit,
    onProceedAnyway: () -> Unit,
    onLogout: () -> Unit,
    modifier: Modifier = Modifier
) {
    val scrollState = rememberScrollState()

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(KiranaCreamBg)
            .statusBarsPadding()
            .navigationBarsPadding()
            .verticalScroll(scrollState)
            .padding(horizontal = 24.dp, vertical = 20.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // ── Header ──────────────────────────────────────────────────────────
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = "नमस्ते, ${profile.merchantName}",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = KiranaGreenPrimary
                )
                Text(
                    text = profile.shopName,
                    fontSize = 13.sp,
                    color = KiranaTextSecondary
                )
            }
            TextButton(onClick = onLogout) {
                Text("Logout", color = KiranaBaakiRed, fontSize = 13.sp)
            }
        }

        Spacer(modifier = Modifier.height(28.dp))

        // ── Title & Explanation ──────────────────────────────────────────────
        AnimatedContent(
            targetState = voiceAuthSession,
            transitionSpec = { fadeIn() togetherWith fadeOut() },
            label = "voiceAuthContent"
        ) { session ->
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                when (session) {
                    is VoiceAuthSession.None -> VoiceVerifyPrompt(
                        isRecording = isRecording,
                        amplitudeRms = amplitudeRms,
                        onStartRecording = onStartRecording,
                        onStopRecording = onStopRecording
                    )

                    is VoiceAuthSession.Verifying -> VerifyingState()

                    is VoiceAuthSession.Inconclusive -> InconclusiveState(
                        reason = session.reason,
                        isRecording = isRecording,
                        amplitudeRms = amplitudeRms,
                        onRetry = onStartRecording,
                        onStopRecording = onStopRecording
                    )

                    is VoiceAuthSession.Rejected -> RejectedState(
                        reason = session.reason,
                        retryAfterMs = session.retryAfterMs,
                        isRecording = isRecording,
                        amplitudeRms = amplitudeRms,
                        onRetry = onStartRecording,
                        onStopRecording = onStopRecording
                    )

                    is VoiceAuthSession.Locked -> LockedState(retryAfterMs = session.retryAfterMs)

                    is VoiceAuthSession.NotEnrolled -> NotEnrolledState(accountId = session.accountId)

                    is VoiceAuthSession.Unavailable -> UnavailableState(
                        reason = session.reason,
                        onProceedAnyway = onProceedAnyway
                    )

                    is VoiceAuthSession.NetworkError -> NetworkErrorState(
                        message = session.message,
                        isRecording = isRecording,
                        amplitudeRms = amplitudeRms,
                        onRetry = onStartRecording,
                        onStopRecording = onStopRecording
                    )

                    is VoiceAuthSession.Verified -> VerifiedState()
                }
            }
        }
    }
}

// ── Voice prompt: initial state and re-try state ───────────────────────────────

@Composable
private fun VoiceVerifyPrompt(
    isRecording: Boolean,
    amplitudeRms: Float,
    onStartRecording: () -> Unit,
    onStopRecording: () -> Unit
) {
    Text(
        text = "आवाज़ से पहचान",
        style = MaterialTheme.typography.headlineSmall,
        fontWeight = FontWeight.Bold,
        color = KiranaTextPrimary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        text = "आपकी दुकान सुरक्षित रखने के लिए आवाज़ सत्यापन ज़रूरी है।",
        style = MaterialTheme.typography.bodyMedium,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )

    Spacer(modifier = Modifier.height(28.dp))

    // Instructions card
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaMintPill),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaMint)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Default.Info, contentDescription = null, tint = KiranaGreenPrimary, modifier = Modifier.size(18.dp))
                Spacer(modifier = Modifier.width(8.dp))
                Text("बोलने के निर्देश", fontWeight = FontWeight.Bold, color = KiranaGreenPrimary, fontSize = 13.sp)
            }
            Spacer(modifier = Modifier.height(10.dp))
            listOf(
                "🎙 नीचे बटन दबाएं और 3–5 सेकंड स्पष्ट बोलें",
                "📢 जोर से और साफ़ बोलें — शांत जगह चुनें",
                "🗣 कुछ भी बोलें — जैसे अपनी दुकान का नाम",
                "🚫 माइक के बहुत पास या बहुत दूर न रहें"
            ).forEach { hint ->
                Text(hint, fontSize = 13.sp, color = KiranaGreenDark, modifier = Modifier.padding(vertical = 2.dp))
            }
        }
    }

    Spacer(modifier = Modifier.height(32.dp))

    // Voice orb + record button
    VoiceCaptureButton(
        isRecording = isRecording,
        amplitudeRms = amplitudeRms,
        onStart = onStartRecording,
        onStop = onStopRecording
    )

    Spacer(modifier = Modifier.height(16.dp))
    Text(
        text = if (isRecording) "बोलिए... (auto-stops on silence)" else "बटन दबाकर बोलें",
        fontSize = 13.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center
    )

    // Audio quality note
    Spacer(modifier = Modifier.height(24.dp))
    Text(
        text = "ℹ आपकी आवाज़ का नमूना सर्वर पर सत्यापन के बाद सुरक्षित रूप से हटा दिया जाता है। कोई ऑडियो डिवाइस पर संग्रहीत नहीं होता।",
        fontSize = 11.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center,
        modifier = Modifier.padding(horizontal = 8.dp)
    )
}

// ── Verifying / uploading ──────────────────────────────────────────────────────

@Composable
private fun VerifyingState() {
    Spacer(modifier = Modifier.height(40.dp))
    CircularProgressIndicator(color = KiranaGreenPrimary, modifier = Modifier.size(56.dp), strokeWidth = 4.dp)
    Spacer(modifier = Modifier.height(20.dp))
    Text(
        "आवाज़ सत्यापित की जा रही है...",
        fontSize = 16.sp,
        fontWeight = FontWeight.SemiBold,
        color = KiranaTextPrimary,
        textAlign = TextAlign.Center
    )
    Text(
        "कृपया प्रतीक्षा करें",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(40.dp))
}

// ── Verified ───────────────────────────────────────────────────────────────────

@Composable
private fun VerifiedState() {
    Spacer(modifier = Modifier.height(40.dp))
    Box(
        modifier = Modifier
            .size(80.dp)
            .clip(CircleShape)
            .background(KiranaJamaGreen),
        contentAlignment = Alignment.Center
    ) {
        Icon(Icons.Default.Check, contentDescription = null, tint = Color.White, modifier = Modifier.size(40.dp))
    }
    Spacer(modifier = Modifier.height(20.dp))
    Text(
        "आवाज़ सत्यापित हो गई! ✓",
        fontSize = 20.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaJamaGreen,
        textAlign = TextAlign.Center
    )
    Text(
        "Dashboard पर जा रहे हैं...",
        fontSize = 14.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(40.dp))
}

// ── Inconclusive (too quiet / too short / insufficient speech) ─────────────────

@Composable
private fun InconclusiveState(
    reason: String,
    isRecording: Boolean,
    amplitudeRms: Float,
    onRetry: () -> Unit,
    onStopRecording: () -> Unit
) {
    StatusIcon(color = Color(0xFFFFA000))  // amber
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "आवाज़ स्पष्ट नहीं थी",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaTextPrimary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        reasonToHindi(reason),
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "⚠ Gateway reason: $reason",
        fontSize = 11.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(28.dp))
    VoiceCaptureButton(isRecording = isRecording, amplitudeRms = amplitudeRms, onStart = onRetry, onStop = onStopRecording)
    Spacer(modifier = Modifier.height(12.dp))
    Text(if (isRecording) "बोलिए... (auto-stops)" else "फिर से बोलें", fontSize = 13.sp, color = KiranaTextMuted)
}

// ── Rejected (no_match) ────────────────────────────────────────────────────────

@Composable
private fun RejectedState(
    reason: String,
    retryAfterMs: Long?,
    isRecording: Boolean,
    amplitudeRms: Float,
    onRetry: () -> Unit,
    onStopRecording: () -> Unit
) {
    StatusIcon(color = KiranaBaakiRed)
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "आवाज़ मेल नहीं खाई",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaBaakiRed,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "आपकी आवाज़ दर्ज नमूने से मेल नहीं खाई। फिर से शांत जगह पर स्पष्ट बोलकर प्रयास करें।",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "⚠ Gateway reason: $reason",
        fontSize = 11.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(28.dp))
    VoiceCaptureButton(isRecording = isRecording, amplitudeRms = amplitudeRms, onStart = onRetry, onStop = onStopRecording)
    Spacer(modifier = Modifier.height(12.dp))
    Text(if (isRecording) "बोलिए... (auto-stops)" else "फिर से प्रयास करें", fontSize = 13.sp, color = KiranaTextMuted)
}

// ── Locked ─────────────────────────────────────────────────────────────────────

@Composable
private fun LockedState(retryAfterMs: Long) {
    StatusIcon(color = KiranaBaakiRed)
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "खाता अस्थायी रूप से बंद",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaBaakiRed,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    val minutesLeft = ((retryAfterMs - System.currentTimeMillis()) / 60000).coerceAtLeast(0)
    Text(
        "बहुत अधिक गलत प्रयास। $minutesLeft मिनट बाद फिर कोशिश करें।\n\n" +
                "यदि समस्या बनी रहे, तो अपने ऑपरेटर से संपर्क करें।",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(16.dp))
    // No retry button — user must wait
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaBaakiRedLight)
    ) {
        Text(
            "Account locked. Wait $minutesLeft min or contact your system administrator to unlock.",
            fontSize = 11.sp,
            color = KiranaBaakiRed,
            modifier = Modifier.padding(12.dp)
        )
    }
}

// ── Not enrolled ────────────────────────────────────────────────────────────────

@Composable
private fun NotEnrolledState(accountId: String) {
    Icon(Icons.Default.Warning, contentDescription = null, tint = Color(0xFFFFA000), modifier = Modifier.size(56.dp))
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "आवाज़ नामांकन नहीं हुआ",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaTextPrimary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "आपके खाते के लिए कोई आवाज़ नमूना दर्ज नहीं है।",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )

    Spacer(modifier = Modifier.height(20.dp))

    // Admin enrollment instructions — strictly from docs/voice_auth.md §4
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaMintPill),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaMint)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Text(
                "व्यवस्थापक से सहायता लें",
                fontWeight = FontWeight.Bold,
                color = KiranaGreenPrimary,
                fontSize = 14.sp
            )
            Spacer(modifier = Modifier.height(10.dp))
            Text(
                "आवाज़ नामांकन केवल एक प्रशिक्षित ऑपरेटर द्वारा किया जा सकता है। " +
                        "Android ऐप स्वयं यह नहीं कर सकती।\n\n" +
                        "अपने ऑपरेटर से कहें:\n" +
                        "• POST /v1/admin/voiceprints/$accountId/enroll\n" +
                        "• न्यूनतम 3 WAV नमूने (3+ सेकंड प्रत्येक)\n" +
                        "• X-Admin-Token प्रमाण-पत्र के साथ\n\n" +
                        "नामांकन के बाद वापस आकर सत्यापन करें।",
                fontSize = 12.sp,
                color = KiranaGreenDark
            )
            Spacer(modifier = Modifier.height(8.dp))
            HorizontalDivider(color = KiranaMint)
            Spacer(modifier = Modifier.height(8.dp))
            Text(
                "ℹ Account ID: $accountId\n" +
                        "Enrollment requires the VAADA_ADMIN_TOKEN (server-side credential). " +
                        "Voice biometric data is never stored on this device.",
                fontSize = 10.sp,
                color = KiranaTextMuted
            )
        }
    }
}

// ── Unavailable (voice auth mode=off or gateway not configured) ─────────────────

@Composable
private fun UnavailableState(reason: String, onProceedAnyway: () -> Unit) {
    Icon(Icons.Default.Info, contentDescription = null, tint = KiranaTextSecondary, modifier = Modifier.size(48.dp))
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "आवाज़ सत्यापन उपलब्ध नहीं",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaTextPrimary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "Gateway पर आवाज़ प्रमाणीकरण अभी सक्रिय नहीं है।",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "Gateway reason: $reason",
        fontSize = 11.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center
    )

    Spacer(modifier = Modifier.height(24.dp))

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaMintPill),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaMint)
    ) {
        Text(
            "Voice authentication is unavailable on this gateway (VAADA_VOICE_AUTH_MODE=off or " +
                    "threshold/model not configured). You can proceed without voice auth — " +
                    "transactions will still require explicit voice confirmation (\"haan\").",
            fontSize = 11.sp,
            color = KiranaGreenDark,
            modifier = Modifier.padding(12.dp)
        )
    }

    Spacer(modifier = Modifier.height(20.dp))

    Button(
        onClick = onProceedAnyway,
        modifier = Modifier.fillMaxWidth().height(54.dp),
        shape = RoundedCornerShape(16.dp),
        colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)
    ) {
        Text("बिना आवाज़ सत्यापन के जारी रखें", fontWeight = FontWeight.Bold, color = Color.White)
    }
}

// ── Network error ───────────────────────────────────────────────────────────────

@Composable
private fun NetworkErrorState(
    message: String,
    isRecording: Boolean,
    amplitudeRms: Float,
    onRetry: () -> Unit,
    onStopRecording: () -> Unit
) {
    StatusIcon(color = KiranaBaakiRed)
    Spacer(modifier = Modifier.height(16.dp))
    Text(
        "Network त्रुटि",
        fontSize = 18.sp,
        fontWeight = FontWeight.Bold,
        color = KiranaBaakiRed,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(8.dp))
    Text(
        "Gateway से संपर्क नहीं हो पाया।",
        fontSize = 13.sp,
        color = KiranaTextSecondary,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(4.dp))
    Text(
        message.take(120),
        fontSize = 11.sp,
        color = KiranaTextMuted,
        textAlign = TextAlign.Center
    )
    Spacer(modifier = Modifier.height(28.dp))
    VoiceCaptureButton(isRecording = isRecording, amplitudeRms = amplitudeRms, onStart = onRetry, onStop = onStopRecording)
    Spacer(modifier = Modifier.height(12.dp))
    Text(if (isRecording) "बोलिए..." else "फिर से प्रयास करें", fontSize = 13.sp, color = KiranaTextMuted)
}

// ── Shared UI components ────────────────────────────────────────────────────────

@Composable
private fun StatusIcon(color: Color) {
    Box(
        modifier = Modifier.size(72.dp).clip(CircleShape).background(color.copy(alpha = 0.12f)),
        contentAlignment = Alignment.Center
    ) {
        Icon(Icons.Default.Warning, contentDescription = null, tint = color, modifier = Modifier.size(36.dp))
    }
}

/**
 * Animated microphone capture button for voice verification recording.
 */
@Composable
private fun VoiceCaptureButton(
    isRecording: Boolean,
    amplitudeRms: Float,
    onStart: () -> Unit,
    onStop: () -> Unit
) {
    val infiniteTransition = rememberInfiniteTransition(label = "verifyPulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.14f,
        animationSpec = infiniteRepeatable(
            animation = tween(900, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "verifyPulseScale"
    )

    val outerScale = if (isRecording) {
        1.0f + (amplitudeRms.coerceIn(0f, 1f) * 0.4f)
    } else {
        1.0f
    }

    Box(
        modifier = Modifier.size(140.dp),
        contentAlignment = Alignment.Center
    ) {
        // Halo ring
        Box(
            modifier = Modifier
                .size(140.dp)
                .scale(if (isRecording) outerScale * 1.1f else pulseScale * 0.95f)
                .clip(CircleShape)
                .background(
                    if (isRecording) KiranaJamaGreen.copy(alpha = 0.18f)
                    else KiranaMint.copy(alpha = 0.5f)
                )
        )
        // Core tappable button
        Button(
            onClick = { if (isRecording) onStop() else onStart() },
            modifier = Modifier.size(96.dp),
            shape = CircleShape,
            colors = ButtonDefaults.buttonColors(
                containerColor = if (isRecording) KiranaBaakiRed else KiranaGreenPrimary
            ),
            border = androidx.compose.foundation.BorderStroke(
                width = 3.dp,
                color = if (isRecording) KiranaBaakiRed.copy(alpha = 0.4f) else KiranaGreenLight
            ),
            contentPadding = PaddingValues(0.dp),
            elevation = ButtonDefaults.buttonElevation(defaultElevation = 6.dp)
        ) {
            Text(
                text = if (isRecording) "⏹" else "🎙",
                fontSize = 32.sp
            )
        }
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

private fun reasonToHindi(reason: String): String = when (reason) {
    "too_short"           -> "रिकॉर्डिंग बहुत छोटी थी — कम से कम 2 सेकंड बोलें"
    "too_quiet"           -> "आवाज़ बहुत धीमी थी — थोड़ा जोर से बोलें"
    "clipped"             -> "आवाज़ बहुत तेज़ थी — माइक से थोड़ा दूर रहें"
    "insufficient_speech" -> "पर्याप्त बोली नहीं पहचानी गई — स्पष्ट और धाराप्रवाह बोलें"
    else                  -> "आवाज़ स्पष्ट नहीं पहचानी गई — फिर से प्रयास करें"
}
