package org.anastylos.capture.ui.pair

import com.google.common.truth.Truth.assertThat
import java.time.Instant
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.anastylos.capture.pairing.ApprovalOutcome
import org.anastylos.capture.pairing.PendingRequest
import org.anastylos.capture.pairing.QrPairClient
import org.anastylos.capture.pairing.RedeemOutcome
import org.anastylos.capture.pairing.VerifyOutcome
import org.anastylos.capture.sync.EphemeralPairingStore
import org.anastylos.capture.sync.QrPairPayload
import org.junit.After
import org.junit.Test

class PairViewModelTest {
    private val now = Instant.parse("2026-10-27T17:00:00Z")

    private fun qrJson(expiresAt: Instant = now.plusSeconds(300)): String =
        "{\"p\":\"anastylos-pair\",\"v\":1,\"w\":\"9f2b7a01c3d44e5f8a0b1c2d3e4f5061\"," +
            "\"u\":\"https://192.168.1.20:8443\"," +
            "\"s\":\"5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206\"," +
            "\"c\":\"anchor banana cherry desert eagle frost\",\"e\":${expiresAt.epochSecond}}"

    private class FakeQrPairClient : QrPairClient {
        val redeems = mutableListOf<Pair<QrPairPayload, String>>()
        val polls = mutableListOf<Pair<QrPairPayload, PendingRequest>>()
        val verifies = mutableListOf<Pair<QrPairPayload, String>>()
        var redeemOutcome: RedeemOutcome = RedeemOutcome.CodeInvalid("unset")
        var approvalOutcome: ApprovalOutcome = ApprovalOutcome.NotApproved("unset")
        var verifyOutcome: VerifyOutcome = VerifyOutcome.Verified("verified-ws")
        var hangApproval = false

        override suspend fun redeem(payload: QrPairPayload, deviceLabel: String): RedeemOutcome {
            redeems += payload to deviceLabel
            return redeemOutcome
        }

        override suspend fun awaitApproval(
            payload: QrPairPayload,
            request: PendingRequest,
        ): ApprovalOutcome {
            polls += payload to request
            if (hangApproval) awaitCancellation()
            return approvalOutcome
        }

        override suspend fun verify(payload: QrPairPayload, token: String): VerifyOutcome {
            verifies += payload to token
            return verifyOutcome
        }
    }

    private fun TestScope.viewModel(client: FakeQrPairClient = FakeQrPairClient()): PairViewModel {
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        return PairViewModel(
            client = client,
            pairingStore = EphemeralPairingStore(),
            deviceLabel = "Test Phone",
            now = { now },
        )
    }

    private fun PairViewModel.screen(): PairScreen = state.value.screen

    private fun PairViewModel.scanValid() {
        onConnectClicked()
        onScanText(qrJson())
    }

    @After
    fun tearDownMain() {
        Dispatchers.resetMain()
    }

    @Test
    fun `valid scan lands on confirm and later scans are ignored`() = runTest {
        val vm = viewModel()
        assertThat(vm.screen()).isEqualTo(PairScreen.Idle)
        vm.onConnectClicked()
        assertThat(vm.screen()).isEqualTo(PairScreen.Scanning)
        vm.onScanText(qrJson())
        val confirm = vm.screen()
        assertThat(confirm).isInstanceOf(PairScreen.Confirm::class.java)
        val payload = (confirm as PairScreen.Confirm).payload
        assertThat(payload.workstationId).isEqualTo("9f2b7a01c3d44e5f8a0b1c2d3e4f5061")
        assertThat(payload.host).isEqualTo("192.168.1.20")
        // Single-fire: a second decode while confirming changes nothing.
        vm.onScanText(qrJson())
        assertThat(vm.screen()).isEqualTo(confirm)
    }

    @Test
    fun `empty frames stay silent but empty barcodes explain`() = runTest {
        val vm = viewModel()
        vm.onConnectClicked()
        vm.onScanText(null)
        assertThat(vm.screen()).isEqualTo(PairScreen.Scanning)
        vm.onScanText("   ")
        assertThat(vm.screen()).isInstanceOf(PairScreen.Invalid::class.java)
    }

    @Test
    fun `garbage scans explain without echoing the payload`() = runTest {
        val vm = viewModel()
        vm.onConnectClicked()
        vm.onScanText("not json at all")
        val screen = vm.screen()
        assertThat(screen).isInstanceOf(PairScreen.Invalid::class.java)
        assertThat((screen as PairScreen.Invalid).message).contains("Not a workstation code")
    }

    @Test
    fun `expired codes are rejected at scan time`() = runTest {
        val vm = viewModel()
        vm.onConnectClicked()
        vm.onScanText(qrJson(expiresAt = now.minusSeconds(1)))
        val screen = vm.screen()
        assertThat(screen).isInstanceOf(PairScreen.Invalid::class.java)
        assertThat((screen as PairScreen.Invalid).message).contains("expired")
    }

    @Test
    fun `cancel and back navigate the early flow`() = runTest {
        val vm = viewModel()
        vm.onConnectClicked()
        vm.onCancelScan()
        assertThat(vm.screen()).isEqualTo(PairScreen.Idle)
        vm.scanValid()
        vm.onBackFromConfirm()
        assertThat(vm.screen()).isEqualTo(PairScreen.Scanning)
    }

    @Test
    fun `confirm redeems polls approves verifies and persists`() = runTest {
        val client = FakeQrPairClient()
        val request = PendingRequest("request-1", now.plusSeconds(300))
        client.redeemOutcome = RedeemOutcome.Pending(request)
        client.approvalOutcome = ApprovalOutcome.Approved("tok-abc", "approval-echo-id")
        // The verify echo is the freshest authenticated identity: it wins
        // over both the approval echo and the advisory scanned id.
        client.verifyOutcome = VerifyOutcome.Verified("verify-echo-id")
        val store = EphemeralPairingStore()
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val vm = PairViewModel(client, store, "Test Phone") { now }

        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()

        val screen = vm.screen()
        assertThat(screen).isInstanceOf(PairScreen.Paired::class.java)
        assertThat((screen as PairScreen.Paired).workstationId).isEqualTo("verify-echo-id")
        assertThat(client.redeems).hasSize(1)
        assertThat(client.redeems.single().second).isEqualTo("Test Phone")
        assertThat(client.polls.single().second).isEqualTo(request)
        assertThat(client.verifies.single().second).isEqualTo("tok-abc")
        val stored = store.load()
        assertThat(stored).isNotNull()
        assertThat(stored!!.token).isEqualTo("tok-abc")
        assertThat(stored.workstationId).isEqualTo("verify-echo-id")
        assertThat(stored.baseUrl).isEqualTo("https://192.168.1.20:8443")
        assertThat(stored.pinnedSpkiSha256Hex)
            .isEqualTo("5b990e9c6186bf1163ff0c7d2cef2f156e92c57a3b4f3cdd43b98800977b9206")
        assertThat(stored.pinnedCertSha256Hex).isEmpty()
    }

    @Test
    fun `consumed codes guide to a fresh QR`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.CodeInvalid("consumed")
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val screen = vm.screen()
        assertThat(screen).isInstanceOf(PairScreen.Failed::class.java)
        val failed = screen as PairScreen.Failed
        assertThat(failed.retryAllowed).isFalse()
        assertThat(failed.message).contains("fresh QR")
        vm.onRetry()
        assertThat(vm.screen()).isEqualTo(PairScreen.Scanning)
    }

    @Test
    fun `unapproved requests guide to a fresh QR`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.Pending(PendingRequest("request-1", now.plusSeconds(60)))
        client.approvalOutcome = ApprovalOutcome.NotApproved("lapsed")
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val screen = vm.screen()
        assertThat(screen).isInstanceOf(PairScreen.Failed::class.java)
        val failed = screen as PairScreen.Failed
        assertThat(failed.retryAllowed).isFalse()
        assertThat(failed.message).contains("Not approved in time")
    }

    @Test
    fun `transient poll failure resumes the same request`() = runTest {
        val client = FakeQrPairClient()
        val request = PendingRequest("request-1", now.plusSeconds(300))
        client.redeemOutcome = RedeemOutcome.Pending(request)
        client.approvalOutcome = ApprovalOutcome.NetworkError("wifi dropped")
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val failed = vm.screen() as PairScreen.Failed
        assertThat(failed.retryAllowed).isTrue()
        assertThat(failed.request).isEqualTo(request)

        client.approvalOutcome = ApprovalOutcome.Approved("tok-abc", "ws")
        vm.onRetry()
        advanceUntilIdle()
        assertThat(vm.screen()).isInstanceOf(PairScreen.Paired::class.java)
        // The consumed code is never re-spent; the live request is re-polled.
        assertThat(client.redeems).hasSize(1)
        assertThat(client.polls).hasSize(2)
        assertThat(client.polls[1].second.requestId).isEqualTo("request-1")
    }

    @Test
    fun `rate-limited redeem allows retry`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.RateLimited(60)
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val failed = vm.screen() as PairScreen.Failed
        assertThat(failed.retryAllowed).isTrue()
        assertThat(failed.request).isNull()

        client.redeemOutcome = RedeemOutcome.Pending(PendingRequest("request-2", now.plusSeconds(300)))
        client.approvalOutcome = ApprovalOutcome.Approved("tok-abc", "ws")
        vm.onRetry()
        advanceUntilIdle()
        assertThat(vm.screen()).isInstanceOf(PairScreen.Paired::class.java)
        assertThat(client.redeems).hasSize(2)
    }

    @Test
    fun `pin mismatch never retries and stores nothing`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.PinMismatch("key differs")
        val store = EphemeralPairingStore()
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val vm = PairViewModel(client, store, "Test Phone") { now }
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val failed = vm.screen() as PairScreen.Failed
        assertThat(failed.retryAllowed).isFalse()
        assertThat(failed.message).contains("identity")
        assertThat(store.load()).isNull()
    }

    @Test
    fun `verify rejection fails closed and stores nothing`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.Pending(PendingRequest("request-1", now.plusSeconds(300)))
        client.approvalOutcome = ApprovalOutcome.Approved("tok-abc", "ws")
        client.verifyOutcome = VerifyOutcome.Unauthorized
        val store = EphemeralPairingStore()
        Dispatchers.setMain(StandardTestDispatcher(testScheduler))
        val vm = PairViewModel(client, store, "Test Phone") { now }
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val failed = vm.screen() as PairScreen.Failed
        assertThat(failed.retryAllowed).isFalse()
        assertThat(failed.message).contains("rejected it")
        assertThat(store.load()).isNull()
    }

    @Test
    fun `verify failure retries verify only`() = runTest {
        val client = FakeQrPairClient()
        val request = PendingRequest("request-1", now.plusSeconds(300))
        client.redeemOutcome = RedeemOutcome.Pending(request)
        client.approvalOutcome = ApprovalOutcome.Approved("tok-abc", "ws")
        client.verifyOutcome = VerifyOutcome.NetworkError("blip")
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        advanceUntilIdle()
        val failed = vm.screen() as PairScreen.Failed
        assertThat(failed.retryAllowed).isTrue()
        assertThat(failed.resumeToken).isEqualTo("tok-abc")
        assertThat(failed.message).contains("approval is kept")

        client.verifyOutcome = VerifyOutcome.Verified("ws")
        vm.onRetry()
        advanceUntilIdle()
        assertThat(vm.screen()).isInstanceOf(PairScreen.Paired::class.java)
        // The spent code is never re-run: one redeem, one poll, two verifies.
        assertThat(client.redeems).hasSize(1)
        assertThat(client.polls).hasSize(1)
        assertThat(client.verifies).hasSize(2)
        assertThat(client.verifies[1].second).isEqualTo("tok-abc")
    }

    @Test
    fun `cancel during pairing abandons cleanly`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.Pending(PendingRequest("request-1", now.plusSeconds(300)))
        client.hangApproval = true
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        runCurrent()
        assertThat(vm.screen()).isInstanceOf(PairScreen.Pairing::class.java)
        vm.onCancelPairing()
        runCurrent()
        assertThat(vm.screen()).isEqualTo(PairScreen.Scanning)
    }

    @Test
    fun `confirm is single-flight`() = runTest {
        val client = FakeQrPairClient()
        client.redeemOutcome = RedeemOutcome.Pending(PendingRequest("request-1", now.plusSeconds(300)))
        client.hangApproval = true
        val vm = viewModel(client)
        vm.scanValid()
        vm.onConfirm()
        vm.onConfirm()
        runCurrent()
        assertThat(client.redeems).hasSize(1)
    }

    @Test
    fun `expiry countdown renders`() {
        assertThat(formatCodeExpiry(now.plusSeconds(272), now)).isEqualTo("expires in 4:32")
        assertThat(formatCodeExpiry(now.plusSeconds(60), now)).isEqualTo("expires in 1:00")
        assertThat(formatCodeExpiry(now.plusSeconds(38), now)).isEqualTo("expires in 38s")
        assertThat(formatCodeExpiry(now, now)).isEqualTo("expired")
        assertThat(formatCodeExpiry(now.minusSeconds(5), now)).isEqualTo("expired")
    }
}
