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

        // Dev bypass — clearly labelled
        TextButton(onClick = onLoginClick) {
            Text(
                text = "⚙ Dev: Skip Auth (Backend not yet connected)",
                fontSize = 11.sp,
                color = KiranaTextMuted
            )
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Phone Number Login Screen
// ─────────────────────────────────────────────────────────────────────────────

@Composable
fun LoginScreen(
    phone: String,
    onPhoneChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onSendOtp: () -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    val focusManager = LocalFocusManager.current

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
            text = "अपना नंबर दर्ज करें",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            color = KiranaTextPrimary
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = "हम OTP भेजकर आपकी पहचान सत्यापित करेंगे।",
            style = MaterialTheme.typography.bodyMedium,
            color = KiranaTextSecondary
        )

        Spacer(modifier = Modifier.height(32.dp))

        OutlinedTextField(
            value = phone,
            onValueChange = { if (it.length <= 10) onPhoneChange(it.filter { c -> c.isDigit() }) },
            label = { Text("मोबाइल नंबर") },
            prefix = { Text("+91  ", color = KiranaTextSecondary) },
            singleLine = true,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Phone,
                imeAction = ImeAction.Done
            ),
            keyboardActions = KeyboardActions(onDone = {
                focusManager.clearFocus()
                if (phone.length == 10) onSendOtp()
            }),
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
            onClick = { focusManager.clearFocus(); onSendOtp() },
            enabled = phone.length == 10 && !loading,
            modifier = Modifier
                .fillMaxWidth()
                .height(54.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
            } else {
                Text("OTP भेजें (Send OTP)", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        // Note: auth backend not yet connected
        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = KiranaMintPill)
        ) {
            Text(
                text = "ℹ Auth backend is not yet connected. OTP will not be sent in this build.",
                fontSize = 11.sp,
                color = KiranaGreenDark,
                modifier = Modifier.padding(12.dp)
            )
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
    merchantName: String,
    shopName: String,
    onMerchantNameChange: (String) -> Unit,
    onShopNameChange: (String) -> Unit,
    loading: Boolean,
    error: String?,
    onSubmit: () -> Unit,
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
            text = "खाता बनाएं",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            color = KiranaTextPrimary
        )
        Spacer(modifier = Modifier.height(6.dp))
        Text(
            text = "अपनी दुकान की जानकारी दर्ज करें।",
            style = MaterialTheme.typography.bodyMedium,
            color = KiranaTextSecondary
        )

        Spacer(modifier = Modifier.height(28.dp))

        OutlinedTextField(
            value = merchantName,
            onValueChange = onMerchantNameChange,
            label = { Text("आपका नाम (Merchant Name)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder,
                focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface
            )
        )

        Spacer(modifier = Modifier.height(14.dp))

        OutlinedTextField(
            value = shopName,
            onValueChange = onShopNameChange,
            label = { Text("दुकान का नाम (Shop Name)") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(14.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = KiranaGreenPrimary,
                unfocusedBorderColor = KiranaBorder,
                focusedContainerColor = KiranaSurface,
                unfocusedContainerColor = KiranaSurface
            )
        )

        AnimatedVisibility(visible = error != null, enter = fadeIn(), exit = fadeOut()) {
            Text(
                text = error ?: "",
                color = KiranaBaakiRed,
                fontSize = 12.sp,
                modifier = Modifier.padding(top = 6.dp, start = 4.dp)
            )
        }

        Spacer(modifier = Modifier.height(28.dp))

        Button(
            onClick = onSubmit,
            enabled = merchantName.isNotBlank() && shopName.isNotBlank() && !loading,
            modifier = Modifier.fillMaxWidth().height(54.dp),
            shape = RoundedCornerShape(16.dp),
            colors = ButtonDefaults.buttonColors(containerColor = KiranaGreenPrimary)
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.size(22.dp), color = Color.White, strokeWidth = 2.dp)
            } else {
                Text("खाता बनाएं (Create Account)", fontSize = 16.sp, fontWeight = FontWeight.Bold, color = Color.White)
            }
        }

        Spacer(modifier = Modifier.height(16.dp))

        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            colors = CardDefaults.cardColors(containerColor = KiranaMintPill)
        ) {
            Text(
                text = "ℹ Auth backend is not yet connected. Account registration is a UI preview.",
                fontSize = 11.sp,
                color = KiranaGreenDark,
                modifier = Modifier.padding(12.dp)
            )
        }
    }
}
