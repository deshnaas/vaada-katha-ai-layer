package com.vaada.ui.settings

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.config.AppConfig
import com.vaada.config.VoiceLanguage
import com.vaada.ui.theme.*

/** Speech-language picker. The choice is persisted and used by the very next voice turn (see AppConfig / VoiceLanguage). */
@Composable
fun LanguageSelector(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var selected by remember { mutableStateOf(AppConfig.voiceLanguage) }
    Column(modifier = modifier.fillMaxWidth()) {
        Text("भाषा / Language", fontSize = 12.sp, fontWeight = FontWeight.Bold, color = KiranaTextMuted)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            VoiceLanguage.entries.forEach { lang ->
                val on = lang == selected
                Surface(
                    modifier = Modifier.weight(1f).clickable {
                        selected = lang
                        AppConfig.setVoiceLanguage(context, lang)
                    },
                    shape = RoundedCornerShape(12.dp),
                    color = if (on) KiranaGreenPrimary else KiranaSurface,
                    border = BorderStroke(1.dp, if (on) KiranaGreenPrimary else KiranaBorder)
                ) {
                    Column(Modifier.padding(horizontal = 8.dp, vertical = 10.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(lang.label, fontSize = 13.sp, fontWeight = FontWeight.Bold,
                            color = if (on) KiranaTextOnPrimary else KiranaTextPrimary)
                        Text(lang.hint, fontSize = 10.sp, color = if (on) KiranaMint else KiranaTextMuted)
                    }
                }
            }
        }
    }
}

/** Editable gateway / ledger-backend addresses (they used to be hard-coded in four network clients). */
@Composable
fun ConnectionSettings(modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var gateway by remember { mutableStateOf(AppConfig.gatewayUrl) }
    var backend by remember { mutableStateOf(AppConfig.backendUrl) }
    var message by remember { mutableStateOf<String?>(null) }
    var ok by remember { mutableStateOf(false) }
    Column(modifier = modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(value = gateway, onValueChange = { gateway = it; message = null }, singleLine = true,
            label = { Text("Voice gateway URL") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(value = backend, onValueChange = { backend = it; message = null }, singleLine = true,
            label = { Text("Ledger backend URL (login + Khata)") }, modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                val err = AppConfig.setUrls(context, gateway, backend)
                ok = err == null
                message = err ?: "Saved. New requests use these addresses."
            }, colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)) { Text("Save") }
            OutlinedButton(onClick = {
                AppConfig.resetUrls(context)
                gateway = AppConfig.gatewayUrl; backend = AppConfig.backendUrl
                ok = true; message = "Defaults restored."
            }) { Text("Reset") }
        }
        message?.let { Text(it, fontSize = 12.sp, color = if (ok) KiranaJamaGreen else KiranaBaakiRed) }
        Text("Phone and servers must be on the same network. Plain http is used on the LAN only.",
            fontSize = 11.sp, color = KiranaTextMuted)
    }
}
