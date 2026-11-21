package com.vaada.config

import android.content.Context

/**
 * Runtime configuration that used to be hard-coded in four network clients: the gateway URL, the ledger backend URL
 * and the speech language. Values persist in SharedPreferences and are read by the clients when they are created.
 */
object AppConfig {
    const val DEFAULT_GATEWAY_URL = "http://10.115.28.169:8100"
    const val DEFAULT_BACKEND_URL = "http://10.115.28.169:8000"

    private const val PREFS = "vaada_config"
    private const val KEY_GATEWAY = "gateway_url"
    private const val KEY_BACKEND = "backend_url"
    private const val KEY_LANGUAGE = "voice_language"

    @Volatile var gatewayUrl: String = DEFAULT_GATEWAY_URL; private set
    @Volatile var backendUrl: String = DEFAULT_BACKEND_URL; private set
    @Volatile var voiceLanguage: VoiceLanguage = VoiceLanguage.DEFAULT; private set

    fun init(context: Context) {
        val p = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        gatewayUrl = normalizeUrl(p.getString(KEY_GATEWAY, null)) ?: DEFAULT_GATEWAY_URL
        backendUrl = normalizeUrl(p.getString(KEY_BACKEND, null)) ?: DEFAULT_BACKEND_URL
        voiceLanguage = VoiceLanguage.fromCode(p.getString(KEY_LANGUAGE, null)) ?: VoiceLanguage.DEFAULT
    }

    fun setVoiceLanguage(context: Context, language: VoiceLanguage) {
        voiceLanguage = language
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_LANGUAGE, language.code).apply()
    }

    /** Returns null on success or a message for the user. */
    fun setUrls(context: Context, gateway: String, backend: String): String? {
        val g = normalizeUrl(gateway) ?: return "Gateway URL must look like http://192.168.1.5:8100"
        val b = normalizeUrl(backend) ?: return "Backend URL must look like http://192.168.1.5:8000"
        gatewayUrl = g
        backendUrl = b
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putString(KEY_GATEWAY, g).putString(KEY_BACKEND, b).apply()
        return null
    }

    fun resetUrls(context: Context) {
        gatewayUrl = DEFAULT_GATEWAY_URL
        backendUrl = DEFAULT_BACKEND_URL
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().remove(KEY_GATEWAY).remove(KEY_BACKEND).apply()
    }

    /** "http(s)://host[:port]" without path, spaces or trailing slash; null when it is not usable. */
    fun normalizeUrl(raw: String?): String? {
        val s = raw?.trim()?.trimEnd('/') ?: return null
        return if (Regex("^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$").matches(s)) s else null
    }
}
