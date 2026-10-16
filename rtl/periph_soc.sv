// APB peripheral subsystem: one APB master port, an address decoder and
// three slaves, plus an interrupt controller.
//
//  0x0000_0000  UART     0x0000_1000  TIMER    0x0000_2000  GPIO
//  0x0000_F000  INTC: 0x00 PENDING R [0] uart [1] timer [2] gpio
//                     0x04 ENABLE RW
//  anything else: PSLVERR, read data 0, no wait states
module periph_soc (
    input  logic        clk, rst_n,
    input  logic        psel, penable, pwrite,
    input  logic [31:0] paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready, pslverr,
    output logic        uart_tx,
    input  logic        uart_rx,
    input  logic [15:0] gpio_in,
    output logic [15:0] gpio_out, gpio_oe,
    output logic        irq
);
    wire [3:0] sel = paddr[15:12];
`ifdef BUG_DECODE
    wire hi_ok = paddr[31:16] == 16'h0;
`else
    wire hi_ok = paddr[31:16] == 16'h0 && paddr[11:8] == 4'h0;
`endif
    wire s_uart = psel && hi_ok && sel == 4'h0;
    wire s_tim = psel && hi_ok && sel == 4'h1;
    wire s_gpio = psel && hi_ok && sel == 4'h2;
    wire s_intc = psel && hi_ok && sel == 4'hF;
    wire s_none = psel && !(s_uart || s_tim || s_gpio || s_intc);

    logic [31:0] rd_u, rd_t, rd_g, rd_i;
    logic rdy_u, rdy_t, rdy_g, err_u, err_t, err_g;
    logic irq_u, irq_t, irq_g;
    logic [2:0] ien;

    apb_uart u_uart (.clk, .rst_n, .psel(s_uart), .penable, .pwrite, .paddr(paddr[7:0]), .pwdata, .prdata(rd_u), .pready(rdy_u),
                     .pslverr(err_u), .tx(uart_tx), .rx(uart_rx), .irq(irq_u));
    apb_timer u_tim (.clk, .rst_n, .psel(s_tim), .penable, .pwrite, .paddr(paddr[7:0]), .pwdata, .prdata(rd_t), .pready(rdy_t),
                     .pslverr(err_t), .irq(irq_t));
    apb_gpio u_gpio (.clk, .rst_n, .psel(s_gpio), .penable, .pwrite, .paddr(paddr[7:0]), .pwdata, .prdata(rd_g), .pready(rdy_g),
                     .pslverr(err_g), .gpio_in, .gpio_out, .gpio_oe, .irq(irq_g));

    wire [2:0] pend = {irq_g, irq_t, irq_u};
    wire fire_i = s_intc && penable;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) ien <= 0;
        else if (fire_i && pwrite && paddr[7:0] == 8'h04) ien <= pwdata[2:0];
    end
`ifdef BUG_INTC_MASK
    assign irq = |(pend & {1'b1, ien[1:0]});
`else
    assign irq = |(pend & ien);
`endif
    always_comb begin
        rd_i = 32'h0;
        if (fire_i && !pwrite) begin
            if (paddr[7:0] == 8'h00) rd_i = {29'h0, pend};
            else if (paddr[7:0] == 8'h04) rd_i = {29'h0, ien};
        end
    end
    wire err_i = fire_i && !(paddr[7:0] == 8'h00 || paddr[7:0] == 8'h04);

    always_comb begin
        prdata = rd_u | rd_t | rd_g | rd_i;  // unselected slaves drive 0
        pready = s_uart ? rdy_u : s_tim ? rdy_t : s_gpio ? rdy_g : (psel && penable);
`ifdef BUG_APB_SLVERR_SETUP
        // protocol bug for the SVA demo: unmapped accesses flag PSLVERR already in the setup cycle
        pslverr = s_uart ? err_u : s_tim ? err_t : s_gpio ? err_g : s_intc ? err_i : s_none;
`else
        pslverr = s_uart ? err_u : s_tim ? err_t : s_gpio ? err_g : s_intc ? err_i : (s_none && penable);
`endif
    end
endmodule
