package com.vaada.config

import com.vaada.network.GatewayErrors
import com.vaada.network.formatPaiseAsRupees
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigAndLanguageTest {
    @Test fun offeredLanguagesAreAllAcceptedByTheGateway() {
        VoiceLanguage.entries.forEach { assertTrue(it.code, VoiceLanguage.isValidGatewayValue(it.code)) }
    }

    @Test fun defaultIsTheHindiEnglishMixTheGatewayDefaultsTo() {
        assertEquals("hi-IN,en-IN", VoiceLanguage.DEFAULT.code)
    }

    @Test fun gatewayValueRulesMirrorTheServer() {
        assertTrue(VoiceLanguage.isValidGatewayValue("indic-auto"))
        assertTrue(VoiceLanguage.isValidGatewayValue("ta-IN"))
        assertFalse(VoiceLanguage.isValidGatewayValue("hi-IN,hi-IN"))      // duplicates refused
        assertFalse(VoiceLanguage.isValidGatewayValue("xx-YY"))
        assertFalse(VoiceLanguage.isValidGatewayValue(""))
        assertFalse(VoiceLanguage.isValidGatewayValue("hi-IN,en-IN,bn-IN,ta-IN,te-IN"))   // more than 4
    }

    @Test fun savedCodeRoundTripsAndUnknownFallsBack() {
        VoiceLanguage.entries.forEach { assertEquals(it, VoiceLanguage.fromCode(it.code)) }
        assertNull(VoiceLanguage.fromCode("ta-IN"))        // supported by STT, deliberately not offered
        assertNull(VoiceLanguage.fromCode(null))
    }

    @Test fun urlNormalisation() {
        assertEquals("http://192.168.1.5:8100", AppConfig.normalizeUrl(" http://192.168.1.5:8100/ "))
        assertEquals("https://vaada.example.com", AppConfig.normalizeUrl("https://vaada.example.com"))
        assertNull(AppConfig.normalizeUrl("192.168.1.5:8100"))
        assertNull(AppConfig.normalizeUrl("http://host/path"))
        assertNull(AppConfig.normalizeUrl("http://ho st:80"))
        assertNull(AppConfig.normalizeUrl("ftp://host"))
        assertNull(AppConfig.normalizeUrl(null))
    }

    @Test fun rupeeFormattingUsesIndianGroupingFromPaise() {
        assertEquals("₹300", formatPaiseAsRupees(30000))
        assertEquals("₹340.50", formatPaiseAsRupees(34050))
        assertEquals("₹1,50,000", formatPaiseAsRupees(15_000_000))
        assertEquals("₹12,345", formatPaiseAsRupees(1_234_500))
        assertEquals("₹0", formatPaiseAsRupees(0))
    }

    @Test fun loginErrorsAreDetectedAndAllOthersGetAMessage() {
        assertTrue(GatewayErrors.needsLogin("MERCHANT_AUTH_REQUIRED"))
        assertFalse(GatewayErrors.needsLogin("STT_TIMEOUT"))
        listOf("BAD_AUDIO", "STT_TIMEOUT", "STT_RATE_LIMITED", "MERCHANT_AUTH_REQUIRED", "WHATEVER").forEach {
            assertTrue(GatewayErrors.friendly(it, null).isNotBlank())
        }
    }
}
