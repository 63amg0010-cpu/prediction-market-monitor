type ClientEnvironment = Pick<NodeJS.ProcessEnv, "NODE_ENV"> &
  Partial<Pick<NodeJS.ProcessEnv, "NEXT_PUBLIC_DISABLE_REACT_DEVTOOLS">>

function reactDeveloperToolsEnabled(environment: ClientEnvironment = process.env): boolean {
  return (
    environment.NODE_ENV === "development" && environment.NEXT_PUBLIC_DISABLE_REACT_DEVTOOLS !== "1"
  )
}

if (reactDeveloperToolsEnabled()) {
  void Promise.all([import("react-grab"), import("react-scan/dist/auto.global.js")])
}
