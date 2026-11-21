package com.vaada.network

/** Turns a gateway error code (backend/voice_gateway/app.py HTTP_STATUS / SPOKEN) into a message a shopkeeper can act on. */
object GatewayErrors {
    /** True when the merchant must log in again (merchant token missing / expired / revoked). */
    fun needsLogin(code: String): Boolean = code == "MERCHANT_AUTH_REQUIRED" || code == "AUTH_REQUIRED" || code == "HTTP_ERROR_401"

    fun friendly(code: String, serverMessage: String?): String = when (code) {
        "MERCHANT_AUTH_REQUIRED" -> "Login ki zaroorat hai. Settings se logout karke dobara login karein."
        "AUTH_REQUIRED" -> "Pehle awaaz se verify karein."
        "BAD_AUDIO" -> "Awaaz theek se record nahi hui. Dobara bolein."
        "AUDIO_TOO_LARGE" -> "Recording bahut lambi hai. Chhota bolein."
        "STT_TIMEOUT", "STT_UNAVAILABLE", "STT_BAD_RESPONSE" -> "Awaaz samajhne ki service abhi busy hai. Thodi der baad try karein."
        "STT_RATE_LIMITED" -> "Bahut jaldi-jaldi bol rahe hain. Thodi der ruk kar bolein."
        "STT_AUTH", "STT_NOT_CONFIGURED" -> "Gateway ki awaaz service set nahi hai (server ki setting)."
        "BAD_REQUEST" -> "Request theek nahi thi. App dobara kholein."
        else -> serverMessage?.takeIf { it.isNotBlank() } ?: "Gateway se jawab nahi mila ($code)."
    }
}

/** Display helper only: the amount always comes from the gateway in paise; nothing is calculated here. */
fun formatPaiseAsRupees(paise: Long): String {
    val rupees = paise / 100
    val rem = (paise % 100).toInt()
    val digits = rupees.toString()
    val grouped = if (digits.length <= 3) digits else {
        val head = digits.dropLast(3)
        val tail = digits.takeLast(3)
        head.reversed().chunked(2).joinToString(",").reversed() + "," + tail
    }
    return "₹" + grouped + if (rem != 0) ".%02d".format(rem) else ""
}
