use std::env;
use std::io::{copy, Result};
use std::net::{TcpListener, TcpStream};
use std::thread;

fn forward(mut client: TcpStream, target_addr: String) -> Result<()> {
    let mut target = TcpStream::connect(&target_addr)?;
    let mut client_clone = client.try_clone()?;
    let mut target_clone = target.try_clone()?;

    thread::spawn(move || {
        let _ = copy(&mut client_clone, &mut target_clone);
    });
    thread::spawn(move || {
        let _ = copy(&mut target, &mut client);
    });
    Ok(())
}

fn listen_and_forward(listen_port: u16, target_addr: String) {
    thread::spawn(move || {
        let bind_addr = format!("0.0.0.0:{}", listen_port);
        let listener = match TcpListener::bind(&bind_addr) {
            Ok(l) => l,
            Err(e) => {
                eprintln!("Failed to bind {}: {}", bind_addr, e);
                return;
            }
        };
        println!("Forwarding {} -> {}", bind_addr, target_addr);
        for stream in listener.incoming() {
            if let Ok(client) = stream {
                let target = target_addr.clone();
                let _ = forward(client, target);
            }
        }
    });
}

fn main() {
    let target_ip = env::var("TARGET_IP").unwrap_or_else(|_| "172.16.0.1".to_string());
    
    // Default ports to forward if FORWARD_PORTS is not set:
    // 8888 (WinBox custom), 8291 (WinBox standard -> 8888), 80 (Web), 443 (Web-SSL), 22 (SSH), 8728 (API), 8729 (API-SSL)
    let default_ports = "8888:8888,8291:8888,80:80,443:443,22:22,8728:8728,8729:8729";
    let ports_str = env::var("FORWARD_PORTS").unwrap_or_else(|_| default_ports.to_string());

    println!("Target RouterOS IP: {}", target_ip);
    println!("Configured FORWARD_PORTS: {}", ports_str);

    for item in ports_str.split(',') {
        let item = item.trim();
        if item.is_empty() {
            continue;
        }

        let (listen_p, target_p) = if item.contains(':') {
            let mut parts = item.split(':');
            let l = parts.next().unwrap_or("").trim().parse::<u16>();
            let t = parts.next().unwrap_or("").trim().parse::<u16>();
            (l, t)
        } else if item.contains("->") {
            let mut parts = item.split("->");
            let l = parts.next().unwrap_or("").trim().parse::<u16>();
            let t = parts.next().unwrap_or("").trim().parse::<u16>();
            (l, t)
        } else {
            let p = item.parse::<u16>();
            (p.clone(), p)
        };

        if let (Ok(lp), Ok(tp)) = (listen_p, target_p) {
            let target_addr = format!("{}:{}", target_ip, tp);
            listen_and_forward(lp, target_addr);
        } else {
            eprintln!("Invalid port format: {}", item);
        }
    }

    loop {
        thread::park();
    }
}
