// Probe: which concurrent-assertion forms does the installed Verilator accept?
// ap1, ap2, ap3, ap4, ap6 and cp1 use forms Verilator 5.020 supports
// (|->, |=>, $stable, $rose, $past, $isunknown, disable iff, cover property).
// ap5, ap7, ap8 and ap9 use ##N, named sequences, [*N] and ##[m:n], which
// 5.020 rejects as UNSUPPORTED. The SVA in this folder uses only the first set.
// Evidence: docs/evidence/2026-10-15/verilator_5020_sva_support_probe.log
module verilator_sva_probe(input logic clk, rst_n, a, b, input logic [7:0] d);
  ap1: assert property (@(posedge clk) disable iff (!rst_n) a |=> b) else $error("ap1");
  ap2: assert property (@(posedge clk) disable iff (!rst_n) a && !b |=> $stable(d)) else $error("ap2");
  ap3: assert property (@(posedge clk) disable iff (!rst_n) $rose(a) |-> !b) else $error("ap3");
  ap4: assert property (@(posedge clk) disable iff (!rst_n) !$isunknown(d)) else $error("ap4");
  ap5: assert property (@(posedge clk) disable iff (!rst_n) a |-> ##1 b) else $error("ap5");
  ap6: assert property (@(posedge clk) disable iff (!rst_n) (a && $past(b)) |-> d == $past(d)) else $error("ap6");
  cp1: cover property (@(posedge clk) a && b);
  sequence s; a ##1 b; endsequence
  ap7: assert property (@(posedge clk) disable iff (!rst_n) s |=> !a) else $error("ap7");
  ap8: assert property (@(posedge clk) disable iff (!rst_n) a |-> b[*2]) else $error("ap8");
  ap9: assert property (@(posedge clk) disable iff (!rst_n) a |-> ##[1:3] b) else $error("ap9");
endmodule
