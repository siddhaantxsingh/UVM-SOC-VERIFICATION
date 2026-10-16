// APB GPIO, 16 pins: output register, output enable, synchronised inputs,
// per-pin rising/falling edge interrupts.
//
//  0x00 OUT   RW     0x04 OE   RW     0x08 IN  R (2-flop synchronised)
//  0x0C RISE_EN RW   0x10 FALL_EN RW  0x14 IRQ_ST R, W1C (sticky edge flags)
module apb_gpio (
    input  logic        clk, rst_n,
    input  logic        psel, penable, pwrite,
    input  logic [7:0]  paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready, pslverr,
    input  logic [15:0] gpio_in,
    output logic [15:0] gpio_out, gpio_oe,
    output logic        irq
);
    logic [15:0] s1, s2, prev, rise_en, fall_en, ist;
    wire fire = psel && penable;
    assign pready = fire;
    assign pslverr = fire && !(paddr[1:0] == 2'b00 && paddr <= 8'h14);
    wire [15:0] rise = s2 & ~prev, fall = ~s2 & prev;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1 <= 0; s2 <= 0; prev <= 0; gpio_out <= 0; gpio_oe <= 0; rise_en <= 0; fall_en <= 0; ist <= 0;
        end else begin
            s1 <= gpio_in;
            s2 <= s1;
            prev <= s2;
            // a new edge in the same cycle as a W1C wins
`ifdef BUG_GPIO_W1C
            ist <= (ist & ~((fire && pwrite && paddr == 8'h14) ? 16'hFFFF : 16'h0)) | (rise & rise_en) | (fall & fall_en);
`else
`ifdef BUG_GPIO_EDGE_LOST
            ist <= (fire && pwrite && paddr == 8'h14) ? (ist & ~pwdata[15:0]) : (ist | (rise & rise_en) | (fall & fall_en));
`else
            ist <= (ist & ~((fire && pwrite && paddr == 8'h14) ? pwdata[15:0] : 16'h0)) | (rise & rise_en) | (fall & fall_en);
`endif
`endif
            if (fire && pwrite) begin
                case (paddr)
                    8'h00: gpio_out <= pwdata[15:0];
                    8'h04: gpio_oe <= pwdata[15:0];
                    8'h0C: rise_en <= pwdata[15:0];
                    8'h10: fall_en <= pwdata[15:0];
                    default: ;
                endcase
            end
        end
    end
    assign irq = |ist;

    always_comb begin
        prdata = 32'h0;
        if (fire && !pwrite) begin
            case (paddr)
                8'h00: prdata = {16'h0, gpio_out};
                8'h04: prdata = {16'h0, gpio_oe};
                8'h08: prdata = {16'h0, s2};
                8'h0C: prdata = {16'h0, rise_en};
                8'h10: prdata = {16'h0, fall_en};
                8'h14: prdata = {16'h0, ist};
                default: prdata = 32'h0;
            endcase
        end
    end
endmodule
