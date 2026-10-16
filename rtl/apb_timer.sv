// APB timer: 32-bit up-counter with prescaler, compare match, one-shot or
// auto-reload, interrupt.
//
//  0x00 CTRL    RW: [0] enable [1] auto_reload [2] irq_en
//  0x04 PRESC   RW: [15:0] counter advances every PRESC+1 clocks
//  0x08 COMPARE RW
//  0x0C COUNT   RW (a write sets the counter)
//  0x10 STATUS  R: [0] match (sticky)  W1C
// On COUNT == COMPARE (when advancing): match is set; with auto_reload the
// counter restarts at 0, otherwise the timer stops (enable clears).
// Zero-wait-state slave; unmapped offsets raise PSLVERR.
module apb_timer (
    input  logic        clk, rst_n,
    input  logic        psel, penable, pwrite,
    input  logic [7:0]  paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready, pslverr,
    output logic        irq
);
    logic enable, reload, irq_en, match;
    logic [15:0] presc, pcnt;
    logic [31:0] compare, count;
    wire fire = psel && penable;
`ifdef BUG_APB_PREADY_SETUP
    assign pready = psel;  // protocol bug for the SVA demo: PREADY already in the setup cycle
`else
    assign pready = fire;
`endif
    assign pslverr = fire && !(paddr[1:0] == 2'b00 && paddr <= 8'h10);
`ifdef BUG_TIMER_PRESC
    wire tick = enable && pcnt >= presc && pcnt != 0;
`else
    wire tick = enable && pcnt == presc;
`endif

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            enable <= 0; reload <= 0; irq_en <= 0; match <= 0;
            presc <= 0; pcnt <= 0; compare <= 32'hFFFF_FFFF; count <= 0;
        end else begin
            if (enable) pcnt <= tick ? 16'd0 : pcnt + 1'b1;
            if (tick) begin
                if (count == compare) begin
                    match <= 1;
`ifdef BUG_TIMER_RELOAD
                    count <= 1;
`else
                    count <= 0;
`endif
                    if (!reload) enable <= 0;
                end else begin
                    count <= count + 1'b1;
                end
            end
            if (fire && pwrite) begin
                case (paddr)
                    8'h00: begin
                        {irq_en, reload, enable} <= pwdata[2:0];
                        pcnt <= 0;
                    end
                    8'h04: presc <= pwdata[15:0];
                    8'h08: compare <= pwdata;
                    8'h0C: count <= pwdata;
                    8'h10: if (pwdata[0]) match <= 0;
                    default: ;
                endcase
            end
        end
    end
    assign irq = match && irq_en;

    always_comb begin
        prdata = 32'h0;
        if (fire && !pwrite) begin
            case (paddr)
                8'h00: prdata = {29'h0, irq_en, reload, enable};
                8'h04: prdata = {16'h0, presc};
                8'h08: prdata = compare;
                8'h0C: prdata = count;
                8'h10: prdata = {31'h0, match};
                default: prdata = 32'h0;
            endcase
        end
    end
endmodule
