import React, { createContext, useContext, useState, useCallback } from 'react';

const MobileSidebarContext = createContext(null);

export const MobileSidebarProvider = ({ children }) => {
    const [isOpen, setIsOpen] = useState(false);

    const open = useCallback(() => setIsOpen(true), []);
    const close = useCallback(() => setIsOpen(false), []);
    const toggle = useCallback(() => setIsOpen((prev) => !prev), []);

    return (
        <MobileSidebarContext.Provider value={{ isOpen, open, close, toggle }}>
            {children}
        </MobileSidebarContext.Provider>
    );
};

export const useMobileSidebar = () => {
    const ctx = useContext(MobileSidebarContext);
    if (!ctx) {
        throw new Error('useMobileSidebar must be used within MobileSidebarProvider');
    }
    return ctx;
};

export default MobileSidebarContext;
