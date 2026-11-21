package com.vaada.ui.assistant

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.models.AssistantState
import com.vaada.models.AudioCaptureMetadata
import com.vaada.ui.theme.KiranaBaakiRed
import com.vaada.ui.theme.KiranaBorder
import com.vaada.ui.theme.KiranaCreamBg
import com.vaada.ui.theme.KiranaGreenDark
import com.vaada.ui.theme.KiranaGreenLight
import com.vaada.ui.theme.KiranaGreenPrimary
import com.vaada.ui.theme.KiranaIcons
import com.vaada.ui.theme.KiranaJamaGreen
import com.vaada.ui.theme.KiranaMint
import com.vaada.ui.theme.KiranaMintPill
import com.vaada.ui.theme.KiranaSurface
import com.vaada.ui.theme.KiranaTextMuted
import com.vaada.ui.theme.KiranaTextPrimary
import com.vaada.ui.theme.KiranaTextSecondary

@Composable
fun HomeScreen(
    state: AssistantState,
    hasPermissions: Boolean,
    lastMetadata: AudioCaptureMetadata?,
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
            // ── 1. Top Bar: Merchant Greeting & Subtle Engine Status Pill ───
            MerchantTopBar(state = state)

            Spacer(modifier = Modifier.height(20.dp))

            // ── Permission Notice (If not granted) ──────────────────────────
            if (!hasPermissions) {
                PermissionNoticeCard(onRequestPermissions = onRequestPermissions)
                Spacer(modifier = Modifier.height(16.dp))
            }

            // ── 2. Friendly Merchant Headline & Status Subtitle ──────────────
            Text(
                text = "नमस्ते! मैं क्या लिखूँ?",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary
            )
            Spacer(modifier = Modifier.height(6.dp))

            val statusSubtitle = when (state) {
                is AssistantState.Stopped -> "सहायक बंद है • नीचे दिए बटन से शुरू करें"
                is AssistantState.Standby -> "“Hey Jarvis” बोलें या बटन दबाकर कहें"
                is AssistantState.Listening -> "सुन रहा हूँ... अपनी बात बोलें"
                is AssistantState.Captured -> "समझ लिया • खाता अपडेट किया जा रहा है"
                is AssistantState.Error -> "त्रुटि: ${state.message}"
            }

            Text(
                text = statusSubtitle,
                style = MaterialTheme.typography.bodyMedium,
                color = if (state is AssistantState.Error) KiranaBaakiRed else KiranaTextSecondary
            )

            Spacer(modifier = Modifier.height(28.dp))

            // ── 3. Beautiful Central Voice Orb ──────────────────────────────
            VoiceOrb(
                state = state,
                onClick = {
                    when {
                        !hasPermissions -> onRequestPermissions()
                        isStopped -> onStartService()
                        isListening -> onStopRecording()
                        else -> onStartRecording()
                    }
                }
            )

            Spacer(modifier = Modifier.height(32.dp))

            // ── 4. Elegant Merchant Conversation / Response Card ────────────
            ConversationCard(
                state = state,
                metadata = lastMetadata
            )
        }

        // ── 5. Ergonomic One-Handed Push-to-Talk Action Button ──────────────
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 24.dp, bottom = 8.dp),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            val buttonColor by animateColorAsState(
                targetValue = when {
                    isListening -> Color(0xFFC62828)
                    isStopped -> KiranaGreenLight
                    else -> KiranaGreenPrimary
                },
                label = "pttButtonColor"
            )

            Button(
                onClick = {
                    when {
                        !hasPermissions -> onRequestPermissions()
                        isListening -> onStopRecording()
                        isStopped -> onStartService()
                        else -> onStartRecording()
                    }
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .height(58.dp),
                shape = RoundedCornerShape(29.dp),
                colors = ButtonDefaults.buttonColors(containerColor = buttonColor),
                elevation = ButtonDefaults.buttonElevation(defaultElevation = 3.dp, pressedElevation = 1.dp)
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center
                ) {
                    Icon(
                        imageVector = when {
                            isListening -> KiranaIcons.Mic
                            isStopped -> Icons.Default.PlayArrow
                            else -> KiranaIcons.Mic
                        },
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(24.dp)
                    )
                    Spacer(modifier = Modifier.width(10.dp))
                    Text(
                        text = when {
                            isListening -> "⏹  बोलना समाप्त करें (Done)"
                            isStopped -> "सहायक चालू करें (Start Assistant)"
                            else -> "🎙  दबाकर बोलें (Push to Talk)"
                        },
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.White
                    )
                }
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = if (isStandby) "हाथ खाली नहीं हैं? बस “Hey Jarvis” बोलें" else "किराना वॉइस खाता",
                fontSize = 12.sp,
                color = KiranaTextMuted
            )
        }
    }
}

// ── Top Bar with Store Name & Subtle Engine Status ───────────────────────────

@Composable
private fun MerchantTopBar(state: AssistantState) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    modifier = Modifier
                        .size(9.dp)
                        .clip(CircleShape)
                        .background(KiranaGreenPrimary)
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = "VAADA KHATA",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.4.sp,
                    color = KiranaGreenPrimary
                )
            }
            Text(
                text = "श्री किराना स्टोर्स",
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                color = KiranaTextPrimary
            )
        }

        // Engine Status Pill
        val isServiceActive = state !is AssistantState.Stopped
        val statusDotColor = when (state) {
            is AssistantState.Listening -> KiranaJamaGreen
            is AssistantState.Standby -> KiranaGreenPrimary
            is AssistantState.Captured -> KiranaGreenLight
            is AssistantState.Error -> KiranaBaakiRed
            else -> KiranaTextMuted
        }
        val statusLabel = when (state) {
            is AssistantState.Listening -> "सुन रहा है"
            is AssistantState.Standby -> "सक्रिय (Active)"
            is AssistantState.Captured -> "प्रोसेसिंग"
            is AssistantState.Error -> "त्रुटि"
            else -> "बंद (Off)"
        }

        Surface(
            shape = RoundedCornerShape(20.dp),
            color = if (isServiceActive) KiranaMintPill else KiranaSurface,
            border = androidx.compose.foundation.BorderStroke(1.dp, if (isServiceActive) KiranaMint else KiranaBorder)
        ) {
            Row(
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .clip(CircleShape)
                        .background(statusDotColor)
                )
                Spacer(modifier = Modifier.width(6.dp))
                Text(
                    text = statusLabel,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = if (isServiceActive) KiranaGreenDark else KiranaTextSecondary
                )
            }
        }
    }
}

// ── Clean Merchant Conversation Card ─────────────────────────────────────────

@Composable
private fun ConversationCard(
    state: AssistantState,
    metadata: AudioCaptureMetadata?
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaSurface),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBorder)
    ) {
        Column(modifier = Modifier.padding(18.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = "नवीनतम लेन-देन (LATEST ACTION)",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 0.8.sp,
                    color = KiranaTextMuted
                )

                if (metadata != null) {
                    Text(
                        text = "${metadata.durationMs / 1000f}s ऑडियो",
                        fontSize = 11.sp,
                        color = KiranaTextMuted
                    )
                }
            }

            Spacer(modifier = Modifier.height(12.dp))

            if (metadata != null) {
                // Utterance Captured & Understood Area
                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        text = "आपने कहा:",
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Medium,
                        color = KiranaTextSecondary
                    )
                    Spacer(modifier = Modifier.height(2.dp))
                    Text(
                        text = "“रमेश को ₹200 उधार लिखो”",
                        fontSize = 16.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = KiranaTextPrimary
                    )

                    Spacer(modifier = Modifier.height(10.dp))

                    Surface(
                        shape = RoundedCornerShape(12.dp),
                        color = KiranaMintPill,
                        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaMint)
                    ) {
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(12.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Text(
                                text = "✓  रमेश के खाते में ₹200 उधार (Baaki) जोड़ दिया गया",
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                color = KiranaGreenDark
                            )
                        }
                    }
                }
            } else {
                // Idle Suggestion Area
                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        text = "ऐसे बोलकर देखें:",
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Medium,
                        color = KiranaTextSecondary
                    )
                    Spacer(modifier = Modifier.height(8.dp))

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        SuggestionChip(text = "“रमेश ₹200 उधार”", modifier = Modifier.weight(1f))
                        SuggestionChip(text = "“सुरेश का जमा ₹500”", modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun SuggestionChip(
    text: String,
    modifier: Modifier = Modifier
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(10.dp),
        color = KiranaCreamBg,
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBorder)
    ) {
        Box(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = text,
                fontSize = 12.sp,
                fontWeight = FontWeight.Medium,
                color = KiranaGreenPrimary
            )
        }
    }
}

// ── Clean Permission Notice ──────────────────────────────────────────────────

@Composable
private fun PermissionNoticeCard(onRequestPermissions: () -> Unit) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = KiranaBaakiRed.copy(alpha = 0.08f)),
        border = androidx.compose.foundation.BorderStroke(1.dp, KiranaBaakiRed.copy(alpha = 0.2f))
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "माइक्रोफ़ोन अनुमति आवश्यक है",
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                    color = KiranaBaakiRed
                )
                Text(
                    text = "आवाज़ से हिसाब लिखने के लिए माइक चालू करें",
                    fontSize = 11.sp,
                    color = KiranaTextSecondary
                )
            }
            Button(
                onClick = onRequestPermissions,
                shape = RoundedCornerShape(8.dp),
                colors = ButtonDefaults.buttonColors(containerColor = KiranaBaakiRed),
                contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 12.dp, vertical = 6.dp)
            ) {
                Text("अनुमति दें", fontSize = 12.sp, color = Color.White)
            }
        }
    }
}
