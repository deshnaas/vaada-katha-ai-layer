package com.vaada.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val KiranaLightColorScheme = lightColorScheme(
    primary = KiranaGreenPrimary,
    onPrimary = KiranaTextOnPrimary,
    primaryContainer = KiranaMint,
    onPrimaryContainer = KiranaGreenDark,
    secondary = KiranaGreenLight,
    onSecondary = KiranaTextOnPrimary,
    secondaryContainer = KiranaMintPill,
    onSecondaryContainer = KiranaGreenDark,
    background = KiranaCreamBg,
    onBackground = KiranaTextPrimary,
    surface = KiranaSurface,
    onSurface = KiranaTextPrimary,
    surfaceVariant = KiranaSurfaceVariant,
    onSurfaceVariant = KiranaTextSecondary,
    outline = KiranaBorder,
    error = KiranaBaakiRed,
    onError = KiranaTextOnPrimary,
    errorContainer = KiranaBaakiRedLight,
    onErrorContainer = KiranaBaakiRed
)

@Composable
fun VaadaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = KiranaLightColorScheme,
        typography = KiranaTypography,
        content = content
    )
}
