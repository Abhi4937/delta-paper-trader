import { redirect } from "next/navigation";

// The strategy builder is a panel on the chain page (Delta-style).
export default function Page() {
  redirect("/chain");
}
