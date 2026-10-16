// Top level of the SV-UVM testbench: clock, reset, DUT, APB interface.
`include "uvm_macros.svh"
module tb_top;
    import uvm_pkg::*;
    import soc_uvm_pkg::*;
    logic clk = 0;
    always #5ns clk = ~clk;
    apb_if bus (clk);
    logic uart_tx, irq;
    logic [15:0] gpio_out, gpio_oe;
    periph_soc dut (
        .clk, .rst_n(bus.rst_n), .psel(bus.psel), .penable(bus.penable), .pwrite(bus.pwrite), .paddr(bus.paddr), .pwdata(bus.pwdata),
        .prdata(bus.prdata), .pready(bus.pready), .pslverr(bus.pslverr), .uart_tx, .uart_rx(1'b1), .gpio_in(16'h0), .gpio_out, .gpio_oe, .irq
    );
    initial begin
        bus.rst_n = 0;
        repeat (3) @(posedge clk);
        bus.rst_n <= 1;
    end
    initial begin
        uvm_config_db#(virtual apb_if)::set(null, "*", "vif", bus);
        run_test();
    end
endmodule
