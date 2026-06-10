import AuthGate from "@/components/AuthGate";
import Shell from "@/components/Shell";

export default function TerminalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <AuthGate>
      <Shell>{children}</Shell>
    </AuthGate>
  );
}
