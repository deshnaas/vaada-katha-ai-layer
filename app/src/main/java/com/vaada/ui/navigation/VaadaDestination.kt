package com.vaada.ui.navigation

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBox
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Settings
import androidx.compose.ui.graphics.vector.ImageVector

enum class VaadaDestination(
    val titleEn: String,
    val titleHi: String,
    val icon: ImageVector
) {
    Assistant(
        titleEn = "Assistant",
        titleHi = "सहायक",
        icon = Icons.Default.Home
    ),
    Ledger(
        titleEn = "Khata",
        titleHi = "खाता",
        icon = Icons.Default.AccountBox
    ),
    Settings(
        titleEn = "Settings",
        titleHi = "सेटिंग्स",
        icon = Icons.Default.Settings
    )
}
