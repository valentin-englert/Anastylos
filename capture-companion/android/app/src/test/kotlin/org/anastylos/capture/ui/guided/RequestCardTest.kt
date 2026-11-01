package org.anastylos.capture.ui.guided

import com.google.common.truth.Truth.assertThat
import org.anastylos.capture.data.GuidedAssociation
import org.anastylos.capture.guidance.DeliveryState
import org.anastylos.capture.guidance.ReceiptKind
import org.anastylos.capture.guidance.RegistrationState
import org.anastylos.capture.guidance.RequestState
import org.anastylos.capture.sync.GuidedViewRequest
import org.junit.Test

class RequestCardTest {
    private val request = GuidedViewRequest(requestId = "r1", targetLabel = "Back shell, inner side up — hands off")
    private val base = GuidanceUiState(remoteRequest = request)

    private fun delivered(receipt: ReceiptKind?) = GuidedAssociation(
        imageId = "img", observationId = "obs", packageId = null, guidedSessionId = "s",
        requestId = "r1", requestRevision = 1, attemptId = "a1", attemptNumber = 1, sidecarRevision = 0,
        deliveryState = DeliveryState.ACKNOWLEDGED, receiptStatus = receipt, updatedAt = java.time.Instant.parse("2026-11-01T00:00:00Z"),
    )

    @Test
    fun noCardWithoutAWorkstationRequest() {
        assertThat(requestCardOf(GuidanceUiState())).isNull()
    }

    @Test
    fun labelIsTheRequestTextVerbatim() {
        val card = requestCardOf(base.copy(registration = RegistrationState.LOST))!!
        assertThat(card.label).isEqualTo("Back shell, inner side up — hands off")
        assertThat(card.hint).isEqualTo("Point the camera at the A3 board")
        assertThat(card.outcome).isNull()
    }

    @Test
    fun receiptsMapToSentOrRetake() {
        val qualified = requestCardOf(base.copy(delivery = delivered(ReceiptKind.QUALIFIED)))!!.outcome!!
        assertThat(qualified.retake).isFalse()
        assertThat(qualified.text).contains("qualified")
        val packageOnly = requestCardOf(base.copy(delivery = delivered(ReceiptKind.PACKAGE_ONLY)))!!.outcome!!
        assertThat(packageOnly.retake).isFalse()
        assertThat(packageOnly.text).contains("not spatially qualified")
        val rejected = requestCardOf(base.copy(delivery = delivered(ReceiptKind.REJECTED)))!!.outcome!!
        assertThat(rejected.retake).isTrue()
    }

    @Test
    fun failedDeliveryAndImageCheckAskForARetakeWithReason() {
        val upload = requestCardOf(base.copy(deliveryNote = "upload failed: timeout"))!!.outcome!!
        assertThat(upload.retake).isTrue()
        assertThat(upload.text).contains("upload failed: timeout")
        val check = requestCardOf(base.copy(request = RequestState.RETAKE_REQUIRED))!!.outcome!!
        assertThat(check.retake).isTrue()
    }
}
