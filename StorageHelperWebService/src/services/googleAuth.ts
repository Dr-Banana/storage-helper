import { Capacitor } from '@capacitor/core'
import { GoogleAuth } from '@codetrix-studio/capacitor-google-auth'

export interface NativeGoogleUser {
  idToken: string
  email: string
  name: string
}

/**
 * Returns true when running inside a native Android/iOS app shell.
 * Returns false when running as a regular web browser (PWA or desktop).
 */
export const isNativePlatform = (): boolean => Capacitor.isNativePlatform()

/**
 * Initialize Google Auth for native platforms.
 * Must be called once when the app starts (e.g., in App.tsx useEffect).
 */
export const initGoogleAuth = (): void => {
  if (isNativePlatform()) {
    GoogleAuth.initialize()
  }
}

/**
 * Trigger the native Google Sign-In sheet (Android/iOS).
 * Returns the idToken to be verified by the backend - same flow as web.
 */
export const nativeGoogleSignIn = async (): Promise<NativeGoogleUser> => {
  const user = await GoogleAuth.signIn()

  if (!user.authentication.idToken) {
    throw new Error('Google sign-in did not return an ID token')
  }

  return {
    idToken: user.authentication.idToken,
    email: user.email,
    name: user.name,
  }
}

/**
 * Sign out from Google on native platforms.
 * Call this alongside the app's own logout logic.
 */
export const nativeGoogleSignOut = async (): Promise<void> => {
  if (!isNativePlatform()) return
  try {
    await GoogleAuth.signOut()
  } catch (error) {
    console.error('Native Google sign-out error:', error)
  }
}
