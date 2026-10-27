package org.anastylos.capture.pairing

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class QrScanTest {
    @Test
    fun `empty frame stays silent`() {
        assertThat(interpretScannedText(null)).isNull()
    }

    @Test
    fun `decoded but empty barcode is unreadable`() {
        assertThat(interpretScannedText(""))
            .isInstanceOf(QrScanResult.Unreadable::class.java)
        assertThat(interpretScannedText("   "))
            .isInstanceOf(QrScanResult.Unreadable::class.java)
    }

    @Test
    fun `text is trimmed before parsing`() {
        val result = interpretScannedText("  {\"p\":\"anastylos-pair\"}  ")
        assertThat(result).isEqualTo(QrScanResult.Valid("{\"p\":\"anastylos-pair\"}"))
    }

    @Test
    fun `label joins manufacturer and model`() {
        assertThat(buildDeviceLabel("Google", "Pixel 8")).isEqualTo("Google Pixel 8")
    }

    @Test
    fun `blank label falls back`() {
        assertThat(buildDeviceLabel(null, null)).isEqualTo("Android phone")
        assertThat(buildDeviceLabel("", "  ")).isEqualTo("Android phone")
    }

    @Test
    fun `control characters are stripped`() {
        assertThat(buildDeviceLabel("Go\u0000ogle", "Pix\u007Fel")).isEqualTo("Google Pixel")
        assertThat(buildDeviceLabel("a\u007Fb", "c")).isEqualTo("ab c")
    }

    @Test
    fun `whitespace runs collapse`() {
        assertThat(buildDeviceLabel("  Google  ", "Pixel  8 Pro")).isEqualTo("Google Pixel 8 Pro")
    }

    @Test
    fun `overlong labels truncate to the receiver limit`() {
        val label = buildDeviceLabel("samsung", "x".repeat(100))
        assertThat(label.length).isEqualTo(DEVICE_LABEL_MAX)
        assertThat(label).startsWith("samsung ")
    }

    @Test
    fun `truncation never leaves a trailing space`() {
        val label = buildDeviceLabel("a".repeat(63), "b")
        assertThat(label).isEqualTo("a".repeat(63))
    }

    @Test
    fun `unicode names survive`() {
        assertThat(buildDeviceLabel("Ünïcödé", "Tëst")).isEqualTo("Ünïcödé Tëst")
    }
}
