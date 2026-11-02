package org.anastylos.capture

import android.os.Bundle
import android.view.KeyEvent
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import org.anastylos.capture.di.AppContainer
import org.anastylos.capture.di.CaptureApp
import org.anastylos.capture.ui.pair.PairViewModel
import org.anastylos.capture.ui.pair.QrPairRoute
import org.anastylos.capture.ui.session.SamplingScreen
import org.anastylos.capture.ui.session.SessionScreen
import org.anastylos.capture.ui.guided.SessionQrScanScreen
import org.anastylos.capture.ui.guided.UNKNOWN_CODE_MESSAGE
import org.anastylos.capture.ui.launcher.LauncherScreen
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import org.anastylos.capture.ui.onboarding.OnboardingPrefs
import org.anastylos.capture.ui.onboarding.OnboardingScreen
import org.anastylos.capture.ui.theme.AnastylosTheme
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import org.anastylos.capture.ui.guided.GuidedCaptureScreen
import org.anastylos.capture.ui.session.SessionViewModel

class MainActivity : ComponentActivity() {
    private val container: AppContainer get() = (application as CaptureApp).container

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent {
            AnastylosApp(container)
        }
    }

    // Volume-down is the push-to-talk fallback, only while voice mode is on.
    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && container.voice.state.value.listening) {
            if (event.repeatCount == 0) container.voice.pushToTalkDown()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onKeyUp(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_VOLUME_DOWN && container.voice.state.value.listening) {
            container.voice.pushToTalkUp()
            return true
        }
        return super.onKeyUp(keyCode, event)
    }
}

@Composable
fun AnastylosApp(container: AppContainer) {
    val context = LocalContext.current
    val onboardingPrefs = remember { OnboardingPrefs(context) }
    AnastylosTheme {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            val navController = rememberNavController()
            // A scanned code hops between screens here: the session to attach, a pairing code to
            // redeem and why the last scan was refused.
            var pendingSession by remember { mutableStateOf<String?>(null) }
            var pendingPairText by remember { mutableStateOf<String?>(null) }
            var scanProblem by remember { mutableStateOf<String?>(null) }
            NavHost(navController = navController, startDestination = "launcher") {
                composable("launcher") {
                    LauncherScreen(onFinished = {
                        val next = if (onboardingPrefs.showAtLaunch) "onboarding" else "session"
                        navController.navigate(next) { popUpTo("launcher") { inclusive = true } }
                    })
                }
                composable("onboarding") {
                    OnboardingScreen(initialShowAtLaunch = onboardingPrefs.showAtLaunch) { showAgain ->
                        onboardingPrefs.showAtLaunch = showAgain
                        if (navController.previousBackStackEntry != null) {
                            navController.popBackStack()
                        } else {
                            navController.navigate("session") { popUpTo("onboarding") { inclusive = true } }
                        }
                    }
                }
                composable("session") {
                    val sessionViewModel: SessionViewModel = viewModel(
                        factory = container.sessionViewModelFactory(),
                    )
                    SessionScreen(
                        sessionViewModel,
                        onConnect = { navController.navigate("pair") },
                        onGuided = { navController.navigate("guided") },
                        onSampling = { navController.navigate("sampling") },
                        onReplayGuide = { navController.navigate("onboarding") },
                    )
                }
                composable("sampling") {
                    val sessionViewModel: SessionViewModel = viewModel(
                        factory = container.sessionViewModelFactory(),
                    )
                    SamplingScreen(
                        sessionViewModel,
                        onBack = { navController.popBackStack() },
                        onReplayGuide = { navController.navigate("onboarding") },
                    )
                }
                composable("guided") {
                    GuidedCaptureScreen(
                        container,
                        onBack = { navController.popBackStack() },
                        onScanSession = { scanProblem = null; navController.navigate("scan-session") },
                        scannedSessionId = pendingSession,
                        scanProblem = scanProblem,
                        onScannedConsumed = { pendingSession = null; scanProblem = null },
                        onReplayGuide = { navController.navigate("onboarding") },
                    )
                }
                composable("scan-session") {
                    SessionQrScanScreen(
                        onSession = { id ->
                            pendingSession = id
                            navController.popBackStack()
                        },
                        onPairingCode = { text ->
                            pendingPairText = text
                            navController.navigate("pair") { popUpTo("scan-session") { inclusive = true } }
                        },
                        onUnknown = {
                            scanProblem = UNKNOWN_CODE_MESSAGE
                            navController.popBackStack()
                        },
                        onCancel = { navController.popBackStack() },
                    )
                }
                composable("pair") {
                    val pairViewModel: PairViewModel = viewModel(
                        factory = container.pairViewModelFactory(),
                    )
                    LaunchedEffect(pendingPairText) {
                        val text = pendingPairText ?: return@LaunchedEffect
                        pendingPairText = null
                        pairViewModel.onConnectClicked()
                        pairViewModel.onScanText(text)
                    }
                    QrPairRoute(
                        pairViewModel,
                        onDone = { navController.popBackStack() },
                        onSessionCode = { id ->
                            pendingSession = id
                            if (!navController.popBackStack("guided", false)) {
                                navController.navigate("guided") { popUpTo("pair") { inclusive = true } }
                            }
                        },
                    )
                }
            }
        }
    }
}
