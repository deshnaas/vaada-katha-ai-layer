package com.vaada.ui.auth

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.vaada.ui.theme.*

// ─────────────────────────────────────────────────────────────────────────────
// Welcome / Onboarding Screen
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun WelcomeScreen(
    onLoginClick: () -> Unit,
    onSignupClick: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(KiranaCreamBg)
            .padding(horizontal = 28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        // Brand mark
        Box(
            modifier = Modifier
                .size(80.dp)
                .clip(RoundedCornerShape(24.dp))
                .background(KiranaGreenPrimary),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = "VK",
                fontSize = 28.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        Text(
            text = "Vaada Khata",
            style = MaterialTheme.typography.displayLarge,
            fontWeight = FontWeight.Bold,
            color = KiranaTextPrimary
        )

        Spacer(modifier = Modifier.height(8.dp))

        Text(
            text = "बोलो। रिकॉर्ड करो। वादा निभाओ।",
            style = MaterialTheme.typography.bodyLarge,
            color = KiranaTextSecondary,
            textAlign = TextAlign.Center
        )

        Spacer(modifier = Modifier.height(12.dp))

        Text(
            text = "Voice-first digital ledger for Indian kirana merchants.",
            style = MaterialTheme.typography.bodyMedium,
            color = KiranaTextMuted,
            textAlign = TextAlign.Center
        )

        Spacer(modifier = Modifier.height(48.dp))

        Button(
            onClick = onLoginClick,
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)
        ) {
            Text(
                text = "लॉगिन करें (Login)",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )
        }

        Spacer(modifier = Modifier.height(14.dp))

        OutlinedButton(
            onClick = onSignupClick,
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp),
            shape = RoundedCornerShape(16.dp),
            border = androidx.compose.foundation.BorderStroke(1.5.dp, KiranaGreenPrimary),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = KiranaGreenPrimary)
        ) {
            Text(
                text = "नया खाता बनाएं (Sign Up)",
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold
            )
        }

        Spacer(modifier = Modifier.height(32.dp))

    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Phone Number Login Screen
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun LoginScreen(
    phone: String,
    pin: String,
    onPhoneChange: (String) -> Unit,
    onPinChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onLogin: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    val focusManager = LocalFocusManager.current
    Column(
        modifier = modifier.fillMaxSize().background(KiranaCreamBg)
            .padding(horizontal = 28.dp).statusBarsPadding(),
        verticalArrangement = Arrangement.Center
    ) {
        TextButton(onClick = onBack, contentPadding = PaddingValues(0.dp)) {
            Text("← वापस जाएं", color = KiranaGreenPrimary, fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.height(24.dp))
        Text("लॉगिन करें", style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold, color = KiranaTextPrimary)
        Spacer(Modifier.height(6.dp))
        Text("अपने मोबाइल नंबर और 6-अंकीय PIN से लॉगिन करें।",
            style = MaterialTheme.typography.bodyMedium, color = KiranaTextSecondary)
        Spacer(Modifier.height(28.dp))
        OutlinedTextField(
            value = phone,
            onValueChange = { onPhoneChange(it.filter(Char::isDigit).take(10)) },
            label = { Text("मोबाइल नंबर") }, prefix = { Text("+91  ", color = KiranaTextSecondary) },
            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder, focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface), isError = error != null
        )
        Spacer(Modifier.height(14.dp))
        OutlinedTextField(
            value = pin,
            onValueChange = { onPinChange(it.filter(Char::isDigit).take(6)) },
            label = { Text("6-अंकीय PIN") }, singleLine = true,
            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus(); if (phone.length == 10 && pin.length == 6) onLogin() }),
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder, focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface), isError = error != null
        )
        AnimatedVisibility(visible = error != null, enter = fadeIn(), exit = fadeOut()) {
            Text(error ?: "", color = KiranaBaakiRed, fontSize = 12.sp,
                modifier = Modifier.padding(top = 6.dp, start = 4.dp))
        }
        Spacer(Modifier.height(24.dp))
        Button(onClick = { focusManager.clearFocus(); onLogin() },
            enabled = phone.length == 10 && pin.length == 6 && !loading,
            modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)) {
            if (loading) CircularProgressIndicator(Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
            else Text("लॉगिन करें (Login)", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// OTP Verification Screen
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun OtpScreen(
    phone: String,
    otp: String,
    onOtpChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onVerify: () -> Unit,
    onResend: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(KiranaCreamBg)
            .padding(horizontal = 28.dp)
            .statusBarsPadding(),
        verticalArrangement = Arrangement.Center
    ) {
        TextButton(onClick = onBack, contentPadding = PaddingValues(0.dp)) {
            Text("← वापस जाएं", color = KiranaGreenPrimary, fontWeight = FontWeight.Medium)
        }

        Spacer(modifier = Modifier.height(24.dp))

        Text(
            text = "OTP दर्ज करें",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            color = KiranaTextPrimary
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = "+91 $phone पर OTP भेजा गया।",
            style = MaterialTheme.typography.bodyMedium,
            color = KiranaTextSecondary
        )

        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = otp,
            onValueChange = { if (it.length <= 6) onOtpChange(it.filter { c -> c.isDigit() }) },
            label = { Text("6-अंकीय OTP") },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.NumberPassword,
                imeAction = ImeAction.Done
            ),
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder,
                focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface
            ),
            isError = error != null
        )

        AnimatedVisibility(visible = error != null, enter = fadeIn(), exit = fadeOut()) {
            Text(
                text = error ?: "",
                color = KiranaBaakiRed,
                fontSize = 12.sp,
                modifier = Modifier.padding(top = 6.dp, start = 4.dp)
            )
        }

        Spacer(modifier = Modifier.height(24.dp))

        Button(
            onClick = onVerify,
            enabled = otp.length == 6 && !loading,
            modifier = Modifier.fillMaxWidth().height(54.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
            } else {
                Text("सत्यापित करें (Verify)", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
            }
        }

        Spacer(modifier = Modifier.height(14.dp))

        TextButton(onClick = onResend) {
            Text("OTP फिर से भेजें (Resend OTP)", color = KiranaGreenPrimary, fontWeight = FontWeight.Medium)
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Merchant Sign-Up Screen
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun SignupScreen(
    phone: String,
    pin: String,
    merchantName: String,
    shopName: String,
    onPhoneChange: (String) -> Unit,
    onPinChange: (String) -> Unit,
    onMerchantNameChange: (String) -> Unit,
    onShopNameChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onSubmit: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier.fillMaxSize().background(KiranaCreamBg)
            .padding(horizontal = 28.dp).statusBarsPadding(),
        verticalArrangement = Arrangement.Center
    ) {
        TextButton(onClick = onBack, contentPadding = PaddingValues(0.dp)) {
            Text("← वापस जाएं", color = KiranaGreenPrimary, fontWeight = FontWeight.Medium)
        }
        Spacer(Modifier.height(20.dp))
        Text("खाता बनाएं", style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold, color = KiranaTextPrimary)
        Spacer(Modifier.height(6.dp))
        Text("अपनी और दुकान की जानकारी भरें।", style = MaterialTheme.typography.bodyMedium,
            color = KiranaTextSecondary)
        Spacer(Modifier.height(22.dp))
        @Composable fun field(value: String, label: String, onChange: (String) -> Unit,
                               keyboard: KeyboardType = KeyboardType.Text) {
            OutlinedTextField(value = value, onValueChange = onChange, label = { Text(label) },
                singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = keyboard),
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
                colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = KiranaGreenPrimary,
                    unfocusedBorderColor = KiranaBorder, focusedContainerColor = KiranaSurface,
                    unfocusedContainerColor = KiranaSurface))
        }
        field(merchantName, "आपका नाम (Merchant Name)", onMerchantNameChange)
        Spacer(Modifier.height(10.dp))
        field(shopName, "दुकान का नाम (Shop Name)", onShopNameChange)
        Spacer(Modifier.height(10.dp))
        field(phone, "10-अंकीय मोबाइल नंबर", { onPhoneChange(it.filter(Char::isDigit).take(10)) }, KeyboardType.Phone)
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(value = pin, onValueChange = { onPinChange(it.filter(Char::isDigit).take(6)) },
            label = { Text("6-अंकीय PIN बनाएं") }, singleLine = true,
            visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder, focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface))
        Text("PIN में 6 अंक होने चाहिए। आसान PIN जैसे 123456 स्वीकार नहीं होंगे।",
            color = KiranaTextMuted, fontSize = 11.sp, modifier = Modifier.padding(top = 6.dp))
        AnimatedVisibility(visible = error != null, enter = fadeIn(), exit = fadeOut()) {
            Text(error ?: "", color = KiranaBaakiRed, fontSize = 12.sp,
                modifier = Modifier.padding(top = 6.dp, start = 4.dp))
        }
        Spacer(Modifier.height(20.dp))
        Button(onClick = onSubmit,
            enabled = merchantName.isNotBlank() && shopName.isNotBlank() && phone.length == 10 && pin.length == 6 && !loading,
            modifier = Modifier.fillMaxWidth().height(54.dp), shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)) {
            if (loading) CircularProgressIndicator(Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
            else Text("खाता बनाएं (Create Account)", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
        }
    }
}
