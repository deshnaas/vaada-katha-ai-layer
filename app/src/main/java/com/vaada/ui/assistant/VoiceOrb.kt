package com.vaada.ui.assistant

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.vaada.models.AssistantState
import com.vaada.ui.theme.KiranaBaakiRed
import com.vaada.ui.theme.KiranaGreenDark
import com.vaada.ui.theme.KiranaGreenLight
import com.vaada.ui.theme.KiranaGreenPrimary
import com.vaada.ui.theme.KiranaIcons
import com.vaada.ui.theme.KiranaMint
import com.vaada.ui.theme.KiranaMintAccent

@Composable
fun VoiceOrb(
    state: AssistantState,
    onClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    val infiniteTransition = rememberInfiniteTransition(label = "orbPulse")

    // Standby breathing animation
    val breathingScale by infiniteTransition.animateFloat(
        initialValue = 1.0f,
        targetValue = 1.08f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1800, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "breathingScale"
    )

    // Halo pulse for Standby
    val haloAlpha by infiniteTransition.animateFloat(
        initialValue = 0.25f,
        targetValue = 0.55f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1800, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "haloAlpha"
    )

    // Amplitude reactivity for Listening state
    val rawRms = if (state is AssistantState.Listening) state.amplitudeRms else 0f
    val animatedRmsScale by animateFloatAsState(
        targetValue = 1.0f + (rawRms.coerceIn(0f, 1f) * 0.45f),
        animationSpec = tween(durationMillis = 120, easing = FastOutSlowInEasing),
        label = "rmsScale"
    )

    val isListening = state is AssistantState.Listening
    val isStandby = state is AssistantState.Standby
    val isCaptured = state is AssistantState.Captured
    val isError = state is AssistantState.Error
    val isStopped = state is AssistantState.Stopped

    Box(
        modifier = modifier
            .size(190.dp)
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = ripple(bounded = false, radius = 95.dp),
                onClick = onClick
            ),
        contentAlignment = Alignment.Center
    ) {
        // ── Outer Animated Halo ──────────────────────────────────────────────
        val haloScale = when {
            isListening -> animatedRmsScale * 1.15f
            isStandby -> breathingScale * 1.12f
            else -> 1.0f
        }

        val haloBrush = when {
            isListening -> Brush.radialGradient(
                colors = listOf(
                    KiranaMintAccent.copy(alpha = 0.45f),
                    KiranaMint.copy(alpha = 0.20f),
                    Color.Transparent
                )
            )
            isStandby -> Brush.radialGradient(
                colors = listOf(
                    KiranaMint.copy(alpha = haloAlpha),
                    KiranaMint.copy(alpha = haloAlpha * 0.4f),
                    Color.Transparent
                )
            )
            isError -> Brush.radialGradient(
                colors = listOf(
                    KiranaBaakiRed.copy(alpha = 0.35f),
                    Color.Transparent
                )
            )
            else -> Brush.radialGradient(
                colors = listOf(
                    Color.Black.copy(alpha = 0.05f),
                    Color.Transparent
                )
            )
        }

        Box(
            modifier = Modifier
                .size(190.dp)
                .scale(haloScale)
                .clip(CircleShape)
                .background(haloBrush)
        )

        // ── Secondary Mid Ripple (Visible when Listening or Standby) ──────────
        if (isListening || isStandby) {
            val midScale = if (isListening) animatedRmsScale * 1.06f else breathingScale
            Box(
                modifier = Modifier
                    .size(150.dp)
                    .scale(midScale)
                    .clip(CircleShape)
                    .background(
                        if (isListening) KiranaMint.copy(alpha = 0.5f)
                        else KiranaMint.copy(alpha = 0.35f)
                    )
            )
        }

        // ── Core Orb Sphere ──────────────────────────────────────────────────
        val coreBrush = when {
            isListening -> Brush.verticalGradient(
                colors = listOf(Color(0xFF2E7D32), Color(0xFF1B5E20))
            )
            isStandby -> Brush.verticalGradient(
                colors = listOf(KiranaGreenLight, KiranaGreenPrimary)
            )
            isCaptured -> Brush.verticalGradient(
                colors = listOf(Color(0xFF2E7D32), KiranaGreenDark)
            )
            isError -> Brush.verticalGradient(
                colors = listOf(Color(0xFFD32F2F), KiranaBaakiRed)
            )
            else -> Brush.verticalGradient(
                colors = listOf(Color(0xFF4A5568), Color(0xFF2D3748))
            )
        }

        Box(
            modifier = Modifier
                .size(116.dp)
                .shadow(
                    elevation = if (isStopped) 4.dp else 12.dp,
                    shape = CircleShape,
                    spotColor = if (isListening || isStandby) KiranaGreenPrimary.copy(alpha = 0.4f) else Color.Black.copy(alpha = 0.2f)
                )
                .clip(CircleShape)
                .background(coreBrush),
            contentAlignment = Alignment.Center
        ) {
            // Central Icon
            when {
                isCaptured -> {
                    Icon(
                        imageVector = Icons.Default.Check,
                        contentDescription = "Success",
                        tint = Color.White,
                        modifier = Modifier.size(44.dp)
                    )
                }
                isError -> {
                    Icon(
                        imageVector = Icons.Default.Warning,
                        contentDescription = "Error",
                        tint = Color.White,
                        modifier = Modifier.size(40.dp)
                    )
                }
                else -> {
                    Icon(
                        imageVector = KiranaIcons.Mic,
                        contentDescription = "Microphone",
                        tint = Color.White.copy(alpha = if (isStopped) 0.65f else 1.0f),
                        modifier = Modifier.size(44.dp)
                    )
                }
            }
        }
    }
}
