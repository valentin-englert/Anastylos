package org.anastylos.capture.camera

import com.google.common.truth.Truth.assertThat
import com.google.common.util.concurrent.ListenableFuture
import java.util.concurrent.CancellationException as JucCancellationException
import java.util.concurrent.ExecutionException
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runTest
import org.junit.Assert.fail
import org.junit.Test

class CameraFuturesTest {
    /** Minimal controllable future: no camera, no threads of its own. */
    private class FakeFuture<T> : ListenableFuture<T> {
        private val listeners = mutableListOf<Pair<Runnable, Executor>>()
        private var value: T? = null
        private var failure: Throwable? = null
        private var cancelled = false
        private var done = false
        var cancelCalls = 0
            private set

        fun complete(v: T) {
            value = v
            done = true
            fire()
        }

        fun fail(e: Throwable) {
            failure = e
            done = true
            fire()
        }

        private fun fire() {
            val pending = listeners.toList()
            listeners.clear()
            pending.forEach { (command, executor) -> executor.execute(command) }
        }

        override fun addListener(listener: Runnable, executor: Executor) {
            if (done || cancelled) executor.execute(listener)
            else listeners += listener to executor
        }

        override fun cancel(mayInterruptIfRunning: Boolean): Boolean {
            cancelCalls++
            cancelled = true
            done = true
            fire()
            return true
        }

        override fun isCancelled(): Boolean = cancelled

        override fun isDone(): Boolean = done

        override fun get(): T {
            if (cancelled) throw JucCancellationException()
            failure?.let { throw ExecutionException(it) }
            if (!done) throw IllegalStateException("not done")
            @Suppress("UNCHECKED_CAST")
            return value as T
        }

        override fun get(timeout: Long, unit: TimeUnit): T = get()
    }

    @Test
    fun delayedFutureSuspendsThenReturnsValue() = runTest {
        val future = FakeFuture<String>()
        val deferred = async { awaitListenableFuture(future, DirectExecutor) }
        // Still suspended: a blocking wait would hang this test instead.
        assertThat(deferred.isCompleted).isFalse()
        future.complete("provider")
        assertThat(deferred.await()).isEqualTo("provider")
    }

    @Test
    fun alreadyCompletedFutureReturnsImmediately() = runTest {
        val future = FakeFuture<String>()
        future.complete("provider")
        assertThat(awaitListenableFuture(future, DirectExecutor)).isEqualTo("provider")
    }

    @Test
    fun failingFuturePropagatesCauseUnwrapped() = runTest {
        val future = FakeFuture<String>()
        future.fail(IllegalStateException("hal wedged"))
        try {
            awaitListenableFuture(future, DirectExecutor)
            fail("expected the provider cause")
        } catch (e: IllegalStateException) {
            assertThat(e).hasMessageThat().isEqualTo("hal wedged")
        }
    }

    @Test
    fun cancelledFutureSurfacesCancellation() = runTest {
        val future = FakeFuture<String>()
        future.cancel(false)
        try {
            awaitListenableFuture(future, DirectExecutor)
            fail("expected cancellation")
        } catch (e: CancellationException) {
            assertThat(e).hasMessageThat().contains("cancelled")
        }
    }

    @Test
    fun coroutineCancellationCancelsFuture() = runTest {
        val future = FakeFuture<String>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            awaitListenableFuture(future, DirectExecutor)
        }
        job.cancel()
        job.join()
        assertThat(job.isCancelled).isTrue()
        assertThat(future.cancelCalls).isEqualTo(1)
    }

    @Test
    fun listenerRunsOnGivenExecutor() = runTest {
        val future = FakeFuture<String>()
        future.complete("provider")
        var ran = false
        val recording = Executor { command ->
            ran = true
            command.run()
        }
        assertThat(awaitListenableFuture(future, recording)).isEqualTo("provider")
        assertThat(ran).isTrue()
    }

    @Test
    fun acquireReturnsNullWhenSourceThrows() = runTest {
        val source = CameraProviderSource { throw IllegalStateException("no backend") }
        assertThat(acquireProviderOrNull(source)).isNull()
    }

    @Test
    fun acquireReturnsNullWhenFutureFails() = runTest {
        val future = FakeFuture<androidx.camera.lifecycle.ProcessCameraProvider>()
        val deferred = async { acquireProviderOrNull(CameraProviderSource { future }) }
        future.fail(IllegalStateException("init error"))
        assertThat(deferred.await()).isNull()
    }

    @Test
    fun acquireReturnsNullWhenFutureCancelledExternally() = runTest {
        val future = FakeFuture<androidx.camera.lifecycle.ProcessCameraProvider>()
        val deferred = async { acquireProviderOrNull(CameraProviderSource { future }) }
        future.cancel(false)
        assertThat(deferred.await()).isNull()
    }

    @Test
    fun acquireRethrowsOwnCancellation() = runTest {
        val future = FakeFuture<androidx.camera.lifecycle.ProcessCameraProvider>()
        val job = launch(start = CoroutineStart.UNDISPATCHED) {
            acquireProviderOrNull(CameraProviderSource { future })
        }
        job.cancel()
        job.join()
        assertThat(job.isCancelled).isTrue()
        assertThat(future.cancelCalls).isEqualTo(1)
    }
}
