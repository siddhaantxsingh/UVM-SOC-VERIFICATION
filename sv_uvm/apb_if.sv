// APB interface between the SV-UVM testbench and periph_soc.
interface apb_if (input logic clk);
    logic        rst_n;
    logic        psel, penable, pwrite;
    logic [31:0] paddr, pwdata, prdata;
    logic        pready, pslverr;
endinterface
