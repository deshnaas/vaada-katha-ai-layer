package com.vaada.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import com.vaada.config.AppConfig
import com.vaada.config.VoiceLanguage

data class VoiceTurnResult(
    val status: String,
    val sessionId: String,
    val transcript: String,
    val replyText: String,
    val replySpeak: List<String>,
    val replyKind: String,
    val dialogueState: String?,
    val awaiting: String?,
    val ledgerCommitted: Boolean,
    val ledgerTransactionId: Int?,
    val ledgerBalancePaise: Long?,
    val duplicate: Boolean,
    val audioStatus: String,
    val audioBase64: String?,
    // Pending action as reported by the gateway (dialogue.pending); null when nothing is pending.
    val pendingCustomer: String? = null,
    val pendingAmountPaise: Long? = null,
    val pendingType: String? = null,
    val pendingNeeds: List<String> = emptyList(),
    val notice: String? = null,
    val transcriptLanguage: String? = null,
    val ledgerType: String? = null
)

private val sharedHttp: OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(15, TimeUnit.SECONDS)
    .readTimeout(60, TimeUnit.SECONDS)
    .writeTimeout(30, TimeUnit.SECONDS)
    .build()

class VoiceGatewayException(
    val errorCode: String,
    message: String,
    val recoverable: Boolean
) : IOException(message)

class VoiceGatewayClient(
    private val baseUrl: String = AppConfig.gatewayUrl
) {
    private val client = sharedHttp

    suspend fun sendTurn(
        audioFile: File,
        sessionId: String,
        merchantToken: String,
        clientMsgId: String = UUID.randomUUID().toString(),
        language: String = AppConfig.voiceLanguage.code
    ): VoiceTurnResult = withContext(Dispatchers.IO) {
        require(VoiceLanguage.isValidGatewayValue(language)) { "Unsupported language: $language" }
        require(sessionId.matches(Regex("[A-Za-z0-9_-]{1,64}"))) {
            "Invalid session ID"
        }
        require(merchantToken.isNotBlank()) {
            "Merchant login token is missing. Please log in again."
        }
        require(clientMsgId.matches(Regex("[A-Za-z0-9_:.\\-]{1,100}"))) {
            "Invalid client message ID"
        }
        require(audioFile.exists() && audioFile.isFile) {
            "Audio file does not exist"
        }
        require(audioFile.extension.equals("wav", ignoreCase = true)) {
            "Gateway requires a WAV audio file"
        }
        require(audioFile.length() in 101..(5L * 1024 * 1024)) {
            "Audio file must be between 101 bytes and 5 MB"
        }

        val audioBody = audioFile.asRequestBody("audio/wav".toMediaType())
        val multipartBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart("audio", audioFile.name, audioBody)
            .addFormDataPart("session_id", sessionId)
            .addFormDataPart("client_msg_id", clientMsgId)
            .addFormDataPart("language", language)
            .build()

        val request = Request.Builder()
            .url("${baseUrl.trimEnd('/')}/v1/voice/turn")
            .header("Authorization", "Bearer ${merchantToken.trim()}")
            .post(multipartBody)
            .build()

        client.newCall(request).execute().use { response ->
            val responseText = response.body?.string()
                ?: throw IOException("Gateway returned an empty response")

            val json = try {
                JSONObject(responseText)
            } catch (e: Exception) {
                throw IOException("Invalid gateway response: ${e.message}")
            }

            if (!response.isSuccessful) {
                val error = json.optJSONObject("error")
                throw VoiceGatewayException(
                    errorCode = error?.optString("code", "HTTP_ERROR") ?: "HTTP_ERROR",
                    message = error?.optString("message", "Gateway request failed")
                        ?: "Gateway request failed",
                    recoverable = error?.optBoolean("recoverable", false) ?: false
                )
            }

            val transcript = json.optJSONObject("transcript")
            val reply = json.optJSONObject("reply")
            val dialogue = json.optJSONObject("dialogue")
            val ledger = json.optJSONObject("ledger")
            val audio = json.optJSONObject("audio")
            val pending = dialogue?.optJSONObject("pending")
            val needs = mutableListOf<String>()
            pending?.optJSONArray("needs")?.let { a -> for (i in 0 until a.length()) needs.add(a.optString(i)) }

            val audioStatus = audio?.optString("status", "unavailable") ?: "unavailable"
            val audioBase64 = audio?.optString("data")
                ?.takeIf { it.isNotBlank() && it != "null" }

            val speakList = mutableListOf<String>()
            val speakArray = reply?.optJSONArray("speak")
            if (speakArray != null) {
                for (i in 0 until speakArray.length()) {
                    val item = speakArray.optString(i)
                    if (item.isNotBlank() && item != "null") speakList.add(item)
                }
            }

            VoiceTurnResult(
                status = json.optString("status", "unknown"),
                sessionId = json.optString("session_id", sessionId),
                transcript = transcript?.optString("text", "") ?: "",
                replyText = reply?.optString("text", "") ?: "",
                replySpeak = speakList,
                replyKind = reply?.optString("kind", "") ?: "",
                dialogueState = dialogue?.optString("state"),
                awaiting = dialogue?.optString("awaiting"),
                ledgerCommitted = ledger?.optBoolean("committed", false) ?: false,
                ledgerTransactionId = if (ledger != null && ledger.has("transaction_id")) {
                    ledger.optInt("transaction_id")
                } else null,
                ledgerBalancePaise = if (ledger != null && ledger.has("balance_paise")) {
                    ledger.optLong("balance_paise")
                } else null,
                duplicate = json.optBoolean("duplicate", false),
                audioStatus = audioStatus,
                audioBase64 = audioBase64,
                pendingCustomer = pending?.optString("customer_name")?.takeIf { it.isNotBlank() && it != "null" },
                pendingAmountPaise = if (pending != null && pending.has("amount_paise") && !pending.isNull("amount_paise")) pending.optLong("amount_paise") else null,
                pendingType = pending?.optString("transaction_type")?.takeIf { it.isNotBlank() && it != "null" },
                pendingNeeds = needs,
                notice = json.optString("notice").takeIf { it.isNotBlank() && it != "null" },
                transcriptLanguage = transcript?.optString("language_code")?.takeIf { it.isNotBlank() && it != "null" },
                ledgerType = ledger?.optString("type")?.takeIf { it.isNotBlank() && it != "null" }
            )
        }
    }
}
