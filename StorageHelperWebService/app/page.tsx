import { redirect } from "next/navigation"

export default function HomePage() {
  // 如果已登录，重定向到 dashboard
  // 否则重定向到登录页
  redirect("/login")
}

