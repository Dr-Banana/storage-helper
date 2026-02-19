import { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.storagehelper.app',
  appName: 'Storage Helper',
  webDir: 'dist',
  plugins: {
    GoogleAuth: {
      scopes: ['profile', 'email'],
      // This is your Web Client ID from Google Cloud Console
      // The same ID used for web login - also required for Android token verification
      serverClientId: '550542205968-6b444q6e5qlfpts2393rjut4i5cclj6r.apps.googleusercontent.com',
      forceCodeForRefreshToken: true,
    },
  },
}

export default config
