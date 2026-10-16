// APB UART: 8 data bits, optional even/odd parity, 1 stop bit, 8-deep TX and
// RX FIFOs, programmable baud divider, interrupts.
//
// Registers (word offsets):
//  0x00 DATA    W: push TX FIFO (ignored when full; sets TX_OVF)  R: pop RX FIFO (0 when empty)
//  0x04 STATUS  R: [0] tx_full [1] tx_empty [2] rx_empty [3] rx_full [4] tx_busy
//  0x08 CTRL    RW: [0] enable [1] parity_en [2] parity_odd
//  0x0C BAUD    RW: [15:0] clocks per bit (minimum 4; smaller values read back as 4)
//  0x10 IRQ_EN  RW: [0] rx_avail [1] tx_empty [2] rx_overrun [3] parity_err [4] frame_err [5] tx_overflow
//  0x14 IRQ_ST  R: pending flags (rx_avail and tx_empty are levels; others sticky)  W1C: sticky bits
//  0x18 RX_COUNT R: [3:0] RX FIFO occupancy
// Unmapped offsets read 0 and raise PSLVERR. Every access takes one wait state.
module apb_uart #(parameter int DEPTH = 8) (
    input  logic        clk, rst_n,
    input  logic        psel, penable, pwrite,
    input  logic [7:0]  paddr,
    input  logic [31:0] pwdata,
    output logic [31:0] prdata,
    output logic        pready, pslverr,
    output logic        tx,
    input  logic        rx,
    output logic        irq
);
    localparam int AW = $clog2(DEPTH);
    logic enable, par_en, par_odd;
    logic [15:0] baud;
    logic [5:0] irq_en;
    logic rx_ovr, par_err, frm_err, tx_ovf;  // sticky

    // ---------------------------------------------------------------- FIFOs
    logic [7:0] txq [DEPTH], rxq [DEPTH];
    logic [AW:0] tx_cnt, rx_cnt;
    logic [AW-1:0] tx_rd, tx_wr, rx_rd, rx_wr;
`ifdef BUG_UART_FIFO_DEPTH
    wire tx_full = tx_cnt == DEPTH[AW:0] - 1'b1, tx_empty = tx_cnt == 0;
`else
    wire tx_full = tx_cnt == DEPTH[AW:0], tx_empty = tx_cnt == 0;
`endif
    wire rx_full = rx_cnt == DEPTH[AW:0], rx_empty = rx_cnt == 0;

    // APB: one wait state (access completes in the second ENABLE cycle)
    logic waited;
    wire access = psel && penable;
`ifdef BUG_UART_NOWAIT
    assign pready = access;
`else
    assign pready = access && waited;
`endif
    wire fire = access && waited;
    wire mapped = paddr[1:0] == 2'b00 && paddr <= 8'h18;
    assign pslverr = fire && !mapped;

    wire push_tx = fire && pwrite && paddr == 8'h00;
    wire pop_rx = fire && !pwrite && paddr == 8'h00 && !rx_empty;

    // receiver produces bytes
    logic rx_valid;
    logic [7:0] rx_byte;
    logic rx_perr, rx_ferr;

    // transmitter consumes bytes
    logic tx_take;
    logic tx_busy;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            waited <= 0;
        end else begin
            waited <= access && !waited;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_cnt <= 0; tx_rd <= 0; tx_wr <= 0;
            rx_cnt <= 0; rx_rd <= 0; rx_wr <= 0;
            enable <= 0; par_en <= 0; par_odd <= 0; baud <= 16'd16; irq_en <= 0;
            rx_ovr <= 0; par_err <= 0; frm_err <= 0; tx_ovf <= 0;
        end else begin
            // TX FIFO
            if (push_tx && !tx_full) begin
                txq[tx_wr] <= pwdata[7:0];
                tx_wr <= tx_wr + 1'b1;
            end
            if (push_tx && tx_full) tx_ovf <= 1;
            if (tx_take) tx_rd <= tx_rd + 1'b1;
            tx_cnt <= tx_cnt + {{AW{1'b0}}, push_tx && !tx_full} - {{AW{1'b0}}, tx_take};
            // RX FIFO (a byte arriving when full is dropped)
            if (rx_valid && !rx_full) begin
                rxq[rx_wr] <= rx_byte;
                rx_wr <= rx_wr + 1'b1;
            end
            if (rx_valid && rx_full) rx_ovr <= 1;
            if (rx_valid && rx_perr) par_err <= 1;
            if (rx_valid && rx_ferr) frm_err <= 1;
            if (pop_rx) rx_rd <= rx_rd + 1'b1;
            rx_cnt <= rx_cnt + {{AW{1'b0}}, rx_valid && !rx_full} - {{AW{1'b0}}, pop_rx};
            // register writes
            if (fire && pwrite) begin
                case (paddr)
                    8'h08: {par_odd, par_en, enable} <= pwdata[2:0];
`ifdef BUG_UART_BAUD_CLAMP
                    8'h0C: baud <= pwdata[15:0] < 16'd2 ? 16'd2 : pwdata[15:0];
`else
                    8'h0C: baud <= pwdata[15:0] < 16'd4 ? 16'd4 : pwdata[15:0];
`endif
                    8'h10: irq_en <= pwdata[5:0];
                    8'h14: begin
`ifdef BUG_UART_W1C
                        if (pwdata[3]) rx_ovr <= 0;
`else
                        if (pwdata[2]) rx_ovr <= 0;
`endif
                        if (pwdata[3]) par_err <= 0;
                        if (pwdata[4]) frm_err <= 0;
                        if (pwdata[5]) tx_ovf <= 0;
                    end
                    default: ;
                endcase
            end
        end
    end

    wire [5:0] pending = {tx_ovf, frm_err, par_err, rx_ovr, tx_empty, !rx_empty};
    assign irq = |(pending & irq_en);

    always_comb begin
        prdata = 32'h0;
        if (fire && !pwrite) begin
            case (paddr)
                8'h00: prdata = rx_empty ? 32'h0 : {24'h0, rxq[rx_rd]};
                8'h04: prdata = {27'h0, tx_busy, rx_full, rx_empty, tx_empty, tx_full};
                8'h08: prdata = {29'h0, par_odd, par_en, enable};
                8'h0C: prdata = {16'h0, baud};
                8'h10: prdata = {26'h0, irq_en};
                8'h14: prdata = {26'h0, pending};
                8'h18: prdata = {{(31 - AW){1'b0}}, rx_cnt};
                default: prdata = 32'h0;
            endcase
        end
    end

    // ---------------------------------------------------------------- transmitter
    // frame: start(0), d0..d7, [parity], stop(1); each bit lasts `baud` clocks
    logic [15:0] tx_div;
    logic [3:0] tx_bit;     // 0 start, 1..8 data, 9 parity (if enabled), last = stop
    logic [7:0] tx_sh;
    logic tx_par;
    assign tx_take = enable && !tx_busy && !tx_empty;
    wire [3:0] tx_last = par_en ? 4'd10 : 4'd9;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_busy <= 0; tx <= 1; tx_div <= 0; tx_bit <= 0; tx_sh <= 0; tx_par <= 0;
        end else if (tx_take) begin
            tx_busy <= 1;
            tx_sh <= txq[tx_rd];
`ifdef BUG_UART_PARITY
            tx_par <= ^txq[tx_rd] ^ ~par_odd;
`else
            tx_par <= ^txq[tx_rd] ^ par_odd;
`endif
            tx_bit <= 0;
            tx_div <= baud - 1'b1;
            tx <= 0;  // start bit
        end else if (tx_busy) begin
            if (tx_div != 0) begin
                tx_div <= tx_div - 1'b1;
            end else if (tx_bit == tx_last) begin
                tx_busy <= 0;  // stop bit done
            end else begin
                tx_bit <= tx_bit + 1'b1;
                tx_div <= baud - 1'b1;
                if (tx_bit < 4'd8) begin
                    tx <= tx_sh[0];
                    tx_sh <= {1'b0, tx_sh[7:1]};
                end else if (tx_bit == 4'd8 && par_en) begin
                    tx <= tx_par;
                end else begin
                    tx <= 1;  // stop
                end
            end
        end
    end

    // ---------------------------------------------------------------- receiver
    // 2-flop synchroniser; a falling edge starts a frame; each bit is sampled
    // in its middle
    logic rx_s1, rx_s2;
    logic rx_busy;
    logic [15:0] rx_div;
    logic [3:0] rx_bit;
    logic [7:0] rx_sh;
    logic rx_par;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_s1 <= 1; rx_s2 <= 1;
            rx_busy <= 0; rx_div <= 0; rx_bit <= 0; rx_sh <= 0; rx_par <= 0;
            rx_valid <= 0; rx_byte <= 0; rx_perr <= 0; rx_ferr <= 0;
        end else begin
            rx_s1 <= rx;
            rx_s2 <= rx_s1;
            rx_valid <= 0;
            if (!rx_busy) begin
                if (enable && !rx_s2) begin
                    rx_busy <= 1;
                    rx_bit <= 0;
`ifdef BUG_RX_SAMPLE
                    rx_div <= baud - 1'b1;
`else
                    rx_div <= {1'b0, baud[15:1]} - 1'b1;  // to the middle of the start bit
`endif
                end
            end else if (rx_div != 0) begin
                rx_div <= rx_div - 1'b1;
            end else begin
                rx_div <= baud - 1'b1;
                rx_bit <= rx_bit + 1'b1;
                if (rx_bit == 0) begin
                    if (rx_s2) rx_busy <= 0;  // glitch, not a start bit
                end else if (rx_bit <= 4'd8) begin
                    rx_sh <= {rx_s2, rx_sh[7:1]};
                end else if (rx_bit == 4'd9 && par_en) begin
                    rx_par <= rx_s2;
                end else begin
                    // stop bit
                    rx_busy <= 0;
                    rx_valid <= 1;
                    rx_byte <= rx_sh;
                    rx_ferr <= !rx_s2;
                    rx_perr <= par_en && (rx_par != (^rx_sh ^ par_odd));
                end
            end
        end
    end
endmodule
