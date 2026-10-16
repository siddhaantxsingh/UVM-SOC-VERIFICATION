// Compact native SystemVerilog UVM environment for periph_soc (IEEE 1800.2,
// Accellera UVM 2020.3.1). One APB agent (sequencer, driver, monitor), a
// register-level scoreboard with its own reference model, a coverage
// collector, sequences and tests.
`include "uvm_macros.svh"
package soc_uvm_pkg;
    import uvm_pkg::*;

    localparam bit [31:0] UART = 32'h0000, TIMER = 32'h1000, GPIO = 32'h2000, INTC = 32'hF000;

    // ------------------------------------------------------------ item
    class apb_item extends uvm_sequence_item;
        bit [31:0] addr;
        bit        write;
        bit [31:0] data;
        int unsigned idle;
        bit [31:0] rdata;
        bit        slverr;
        int unsigned waits;
        `uvm_object_utils(apb_item)
        function new(string name = "apb_item"); super.new(name); endfunction
        function string convert2string();
            return $sformatf("%s addr=%08h data=%08h rdata=%08h err=%0d waits=%0d", write ? "W" : "R", addr, data, rdata, slverr, waits);
        endfunction
    endclass

    // ------------------------------------------------------------ driver
    class apb_driver extends uvm_driver #(apb_item);
        `uvm_component_utils(apb_driver)
        virtual apb_if vif;
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        function void build_phase(uvm_phase phase);
            if (!uvm_config_db#(virtual apb_if)::get(this, "", "vif", vif)) `uvm_fatal("NOVIF", "no apb_if")
        endfunction
        task run_phase(uvm_phase phase);
            vif.psel <= 0; vif.penable <= 0; vif.pwrite <= 0; vif.paddr <= 0; vif.pwdata <= 0;
            @(posedge vif.clk iff vif.rst_n);
            forever begin
                seq_item_port.get_next_item(req);
                repeat (req.idle) @(posedge vif.clk);
                vif.psel <= 1; vif.penable <= 0; vif.pwrite <= req.write; vif.paddr <= req.addr; vif.pwdata <= req.write ? req.data : 0;
                @(posedge vif.clk);
                vif.penable <= 1;
                req.waits = 0;
                forever begin
                    @(posedge vif.clk);
                    if (vif.pready) break;
                    req.waits++;
                end
                req.rdata = vif.prdata;
                req.slverr = vif.pslverr;
                vif.psel <= 0; vif.penable <= 0;
                seq_item_port.item_done();
            end
        endtask
    endclass

    // ------------------------------------------------------------ monitor
    class apb_monitor extends uvm_monitor;
        `uvm_component_utils(apb_monitor)
        virtual apb_if vif;
        uvm_analysis_port #(apb_item) ap;
        function new(string name, uvm_component parent); super.new(name, parent); ap = new("ap", this); endfunction
        function void build_phase(uvm_phase phase);
            if (!uvm_config_db#(virtual apb_if)::get(this, "", "vif", vif)) `uvm_fatal("NOVIF", "no apb_if")
        endfunction
        task run_phase(uvm_phase phase);
            int unsigned waits = 0;
            forever begin
                @(posedge vif.clk);
                if (vif.rst_n && vif.psel && vif.penable) begin
                    if (vif.pready) begin
                        apb_item t = apb_item::type_id::create("t");
                        t.addr = vif.paddr; t.write = vif.pwrite; t.data = vif.pwdata;
                        t.rdata = vif.prdata; t.slverr = vif.pslverr; t.waits = waits;
                        ap.write(t);
                        waits = 0;
                    end else waits++;
                end
            end
        endtask
    endclass

    // ------------------------------------------------------------ agent
    class apb_agent extends uvm_agent;
        `uvm_component_utils(apb_agent)
        uvm_sequencer #(apb_item) sqr;
        apb_driver drv;
        apb_monitor mon;
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        function void build_phase(uvm_phase phase);
            sqr = uvm_sequencer#(apb_item)::type_id::create("sqr", this);
            drv = apb_driver::type_id::create("drv", this);
            mon = apb_monitor::type_id::create("mon", this);
        endfunction
        function void connect_phase(uvm_phase phase);
            drv.seq_item_port.connect(sqr.seq_item_export);
        endfunction
    endclass

    // ------------------------------------------------------------ scoreboard
    // Register-level reference model written from the register map
    // (regs/periph_soc.json): address decode and PSLVERR, reset values, RW
    // masks, the BAUD clamp, wait states. Registers whose value changes on
    // its own (FIFOs, status, counters, pending flags) are checked for
    // PSLVERR only.
    class soc_scoreboard extends uvm_component;
        `uvm_component_utils(soc_scoreboard)
        uvm_analysis_imp #(apb_item, soc_scoreboard) imp;
        bit [31:0] regs[bit [31:0]];
        int unsigned n_checked, n_errors;
        function new(string name, uvm_component parent); super.new(name, parent); imp = new("imp", this); endfunction

        function void build_phase(uvm_phase phase);
            regs[UART + 'h08] = 0;  regs[UART + 'h0C] = 16; regs[UART + 'h10] = 0;
            regs[TIMER + 'h00] = 0; regs[TIMER + 'h04] = 0; regs[TIMER + 'h08] = 32'hFFFF_FFFF;
            regs[GPIO + 'h00] = 0;  regs[GPIO + 'h04] = 0;  regs[GPIO + 'h0C] = 0; regs[GPIO + 'h10] = 0;
            regs[INTC + 'h04] = 0;
        endfunction

        static function bit mapped(bit [31:0] a);
            if (a[31:16] != 0 || a[11:8] != 0 || a[1:0] != 0) return 0;
            case (a[15:12])
                4'h0: return a[7:0] <= 8'h18;
                4'h1: return a[7:0] <= 8'h10;
                4'h2: return a[7:0] <= 8'h14;
                4'hF: return a[7:0] == 8'h00 || a[7:0] == 8'h04;
                default: return 0;
            endcase
        endfunction

        static function bit [31:0] wmask(bit [31:0] a);
            case (a)
                UART + 'h08: return 32'h7;     UART + 'h0C: return 32'hFFFF; UART + 'h10: return 32'h3F;
                TIMER + 'h00: return 32'h7;    TIMER + 'h04: return 32'hFFFF; TIMER + 'h08: return 32'hFFFF_FFFF;
                GPIO + 'h00, GPIO + 'h04, GPIO + 'h0C, GPIO + 'h10: return 32'hFFFF;
                INTC + 'h04: return 32'h7;
                default: return 0;
            endcase
        endfunction

        function void err(string msg);
            n_errors++;
            `uvm_error("SCB", msg)
        endfunction

        function void write(apb_item t);
            bit m = mapped(t.addr);
            n_checked++;
            if (t.slverr != !m) err($sformatf("PSLVERR=%0d for %s (expected %0d)", t.slverr, t.convert2string(), !m));
            if (t.addr[15:12] == 4'h0 && m && t.waits != 1) err($sformatf("UART access with %0d wait states (expected 1): %s", t.waits, t.convert2string()));
            if (!m || !regs.exists(t.addr)) begin
                if (!t.write && !m && t.rdata != 0) err($sformatf("unmapped read returned %08h", t.rdata));
                return;
            end
            if (t.write) begin
                bit [31:0] v = t.data & wmask(t.addr);
                if (t.addr == UART + 'h0C && v < 4) v = 4;  // baud divider clamps at 4
                regs[t.addr] = v;
            end else if (t.rdata != regs[t.addr]) begin
                err($sformatf("read %08h, model %08h: %s", t.rdata, regs[t.addr], t.convert2string()));
            end
        endfunction

        function void report_phase(uvm_phase phase);
            `uvm_info("SCB", $sformatf("%0d transfers checked, %0d mismatches", n_checked, n_errors), UVM_LOW)
            if (n_checked == 0) `uvm_error("SCB", "no transfers checked")
        endfunction
    endclass

    // ------------------------------------------------------------ coverage
    // Simple bin counting (no covergroups, to stay within what every
    // simulator in the flow supports).
    class soc_coverage extends uvm_subscriber #(apb_item);
        `uvm_component_utils(soc_coverage)
        int unsigned hits[string];
        string bin_names[$];
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        function void build_phase(uvm_phase phase);
            bin_names = '{"uart.w", "uart.r", "timer.w", "timer.r", "gpio.w", "gpio.r", "intc.w", "intc.r", "slverr", "wait_state"};
            foreach (bin_names[i]) hits[bin_names[i]] = 0;
        endfunction
        function void write(apb_item t);
            string blk;
            case (t.addr[15:12]) 4'h0: blk = "uart"; 4'h1: blk = "timer"; 4'h2: blk = "gpio"; 4'hF: blk = "intc"; default: blk = ""; endcase
            if (t.slverr) hits["slverr"]++;
            else if (blk != "") hits[{blk, t.write ? ".w" : ".r"}]++;
            if (t.waits) hits["wait_state"]++;
        endfunction
        function void report_phase(uvm_phase phase);
            int unsigned n = 0;
            foreach (bin_names[i]) if (hits[bin_names[i]]) n++;
            `uvm_info("COV", $sformatf("functional coverage %0d/%0d bin_names", n, bin_names.size()), UVM_LOW)
        endfunction
    endclass

    // ------------------------------------------------------------ env
    class soc_env extends uvm_env;
        `uvm_component_utils(soc_env)
        apb_agent agt;
        soc_scoreboard scb;
        soc_coverage cov;
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        function void build_phase(uvm_phase phase);
            agt = apb_agent::type_id::create("agt", this);
            scb = soc_scoreboard::type_id::create("scb", this);
            cov = soc_coverage::type_id::create("cov", this);
        endfunction
        function void connect_phase(uvm_phase phase);
            agt.mon.ap.connect(scb.imp);
            agt.mon.ap.connect(cov.analysis_export);
        endfunction
    endclass

    // ------------------------------------------------------------ sequences
    // Random writes and reads to the registers the scoreboard models.
    class reg_rw_seq extends uvm_sequence #(apb_item);
        `uvm_object_utils(reg_rw_seq)
        int unsigned n = 200;
        function new(string name = "reg_rw_seq"); super.new(name); endfunction
        task body();
            bit [31:0] addrs[$] = '{UART + 'h08, UART + 'h0C, UART + 'h10, TIMER + 'h04, TIMER + 'h08,
                                    GPIO + 'h00, GPIO + 'h04, GPIO + 'h0C, GPIO + 'h10, INTC + 'h04};
            repeat (n) begin
                apb_item it = apb_item::type_id::create("it");
                start_item(it);
                // procedural randomisation ($urandom); no constraint solver needed
                it.addr = addrs[$urandom_range(addrs.size() - 1)];
                it.write = $urandom_range(1);
                it.data = $urandom;
                it.idle = $urandom_range(2);
                finish_item(it);
            end
        endtask
    endclass

    // Accesses to unmapped blocks, unmapped offsets and the decoder holes
    // (address bits 11:8), mixed with reads of every mapped register.
    class decode_err_seq extends uvm_sequence #(apb_item);
        `uvm_object_utils(decode_err_seq)
        int unsigned n = 200;
        function new(string name = "decode_err_seq"); super.new(name); endfunction
        function int pick_block();
            int blocks[6] = '{0, 1, 2, 3, 7, 15};
            return blocks[$urandom_range(5)];
        endfunction
        task body();
            repeat (n) begin
                apb_item it = apb_item::type_id::create("it");
                start_item(it);
                it.idle = $urandom_range(2);
                it.addr[31:16] = $urandom_range(9) == 0 ? 16'($urandom_range(16'hFFFF, 1)) : 16'h0;
                it.addr[15:12] = 4'(pick_block());
                it.addr[11:8] = $urandom_range(3) == 0 ? 4'($urandom_range(15, 1)) : 4'h0;
                it.addr[7:0] = 8'($urandom_range(8'h20));
                it.addr[1:0] = 2'b00;
                // writes only where they have no side effects on UART/timer state
                it.write = it.addr[15:12] != 4'h0 && !(it.addr[15:12] == 4'h1 && it.addr[7:0] == 8'h00) && $urandom_range(1);
                if (!it.write && it.addr[15:0] == 16'h0000) it.addr[7:0] = 8'h04;  // never pop the RX FIFO
                it.data = $urandom;
                finish_item(it);
            end
        endtask
    endclass

    // ------------------------------------------------------------ tests
    class soc_base_test extends uvm_test;
        `uvm_component_utils(soc_base_test)
        soc_env env;
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        function void build_phase(uvm_phase phase);
            env = soc_env::type_id::create("env", this);
        endfunction
        virtual task body(); endtask
        task run_phase(uvm_phase phase);
            phase.raise_objection(this);
            body();
            #100ns;
            phase.drop_objection(this);
        endtask
    endclass

    class soc_reg_test extends soc_base_test;
        `uvm_component_utils(soc_reg_test)
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        task body();
            reg_rw_seq s = reg_rw_seq::type_id::create("s");
            s.start(env.agt.sqr);
        endtask
    endclass

    class soc_decode_test extends soc_base_test;
        `uvm_component_utils(soc_decode_test)
        function new(string name, uvm_component parent); super.new(name, parent); endfunction
        task body();
            decode_err_seq s = decode_err_seq::type_id::create("s");
            reg_rw_seq r = reg_rw_seq::type_id::create("r");
            r.n = 50;
            s.start(env.agt.sqr);
            r.start(env.agt.sqr);
        endtask
    endclass
endpackage
