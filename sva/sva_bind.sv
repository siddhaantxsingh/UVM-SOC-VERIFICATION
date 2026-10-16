// Attaches the assertion modules in soc_sva.sv to the RTL. Ports are
// connected by name (.*) to signals inside each bound module.
bind periph_soc apb_soc_sva u_sva (.*);
bind apb_uart apb_uart_sva #(.DEPTH(DEPTH)) u_sva (.*);
bind apb_timer apb_timer_sva u_sva (.*);
bind apb_gpio apb_gpio_sva u_sva (.*);
