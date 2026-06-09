import Shell from "@/components/Shell";

export default function TerminalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <Shell>{children}</Shell>;
}
